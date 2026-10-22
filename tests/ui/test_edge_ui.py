import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.runtime import EdgeSettings, resolve_edge_settings  # noqa: E402
from src.ui.widgets.service_status_panel import (  # noqa: E402
    SERVICES,
    ServiceStatusPanel,
)
from src.ui.widgets.settings_dialog import EndpointsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_services_labels():
    assert SERVICES == {
        "asr": "ASR (rt-asr)",
        "llm": "Переклад (LLM)",
        "tts": "TTS (rt-tts)",
    }


def test_status_panel_shows_lease(app):
    panel = ServiceStatusPanel()
    panel.llm_source = "lease"
    panel.update_status(
        {
            "pinned": {"asr": "http://a:1", "tts": None},
            "lease": {
                "service": "w1",
                "model": "m",
                "expires_at": "2026-01-01T00:00:00",
            },
            "health": {
                "asr": [{"ready": True}],
                "tts": [],
                "llm": [{"ready": True, "lease": None}],
            },
        }
    )
    detail = panel._details["llm"].text()
    assert "w1" in detail and "2026-01-01" in detail and "lease" in detail
    assert panel._texts["asr"].text() == "1/1 готові"
    assert "http://a:1" in panel._details["asr"].text()


def test_endpoints_dialog_values_and_segmentation(app):
    s = resolve_edge_settings(
        {"services": {"asr_urls": ["http://x:1"], "tts_urls": ["http://t:2"]}}, {}
    )
    dlg = EndpointsDialog(s)
    dlg.endpoints_tab.asr_urls.setPlainText("http://a:1\nhttp://b:2")
    dlg.segmentation_tab.chunk_min_s.setValue(120)
    dlg.segmentation_tab.end_silence_ms.setValue(400)
    v = dlg.values()
    assert v["services.asr_urls"] == ["http://a:1", "http://b:2"]
    assert v["segmentation.transcribe.chunk_min_s"] == 120
    assert v["segmentation.translate.end_silence_ms"] == 400
    assert v["services.mode"] == "auto"


def test_managed_fields_disabled(app):
    s = resolve_edge_settings({}, {"RT_EDGE_ASR_URLS": "http://e:1"})
    dlg = EndpointsDialog(s)
    assert not dlg.endpoints_tab.asr_urls.isEnabled()
    assert isinstance(s, EdgeSettings)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from src.core import config as cfgmod
    from src.core import runtime

    for k in list(os.environ):
        if k.startswith("RT_EDGE_"):
            monkeypatch.delenv(k)
    mgr = cfgmod.ConfigManager(str(tmp_path))
    monkeypatch.setattr(cfgmod, "_config_manager", mgr)
    runtime.reset_runtime_config()
    yield mgr
    runtime.reset_runtime_config()


def _saved(mgr):
    import yaml

    return yaml.safe_load(open(mgr.config_file)) if mgr.config_file.exists() else {}


def test_accept_persists_and_emits(app, cfg):
    from src.ui.widgets.settings_dialog import current_settings

    dlg = EndpointsDialog(current_settings())
    got = []
    dlg.settings_changed.connect(got.append)
    dlg.endpoints_tab.asr_urls.setPlainText("http://a:1")
    dlg.segmentation_tab.end_silence_off.setChecked(True)
    dlg.accept()
    assert got and got[0].asr_urls == ("http://a:1",)
    assert got[0].segmentation.translate.end_silence_ms is None
    assert _saved(cfg)["services"]["asr_urls"] == ["http://a:1"]


def test_invalid_keeps_dialog_open_and_unsaved(app, cfg):
    from src.ui.widgets.settings_dialog import current_settings

    dlg = EndpointsDialog(current_settings())
    dlg.endpoints_tab.mode.setCurrentText("translate")
    dlg.accept()
    assert "translate mode" in dlg.error_label.text()
    assert dlg.result() == 0
    assert "mode" not in (_saved(cfg).get("services") or {})


def test_managed_not_saved(app, cfg, monkeypatch):
    from src.ui.widgets.settings_dialog import current_settings

    monkeypatch.setenv("RT_EDGE_ASR_URLS", "http://env:1")
    dlg = EndpointsDialog(current_settings())
    dlg.endpoints_tab.model.setText("m2")
    dlg.accept()
    saved = _saved(cfg)["services"]
    assert "asr_urls" not in saved
    assert saved["llm"]["model"] == "m2"


def test_main_window_constructs(app, cfg):
    import time
    from unittest.mock import MagicMock

    from src.ui.widgets.main_window import MainWindow

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    ctl.get_status.return_value = {"running": True}
    win = MainWindow(ctl)
    assert win.service_status_panel.get_service_names() == ["asr", "llm", "tts"]
    win._apply_ui_update({"input": 0.5, "health": {}})
    win.on_general_settings_changed(resolve_edge_settings({}, {}))
    for _ in range(100):
        app.processEvents()
        if not win._busy:
            break
        time.sleep(0.02)
    win.toggle_translation()
    assert not win.translate_button.isEnabled()
    for _ in range(100):
        app.processEvents()
        if win.translate_button.isEnabled():
            break
        time.sleep(0.02)
    assert win.translate_button.text() == "Stop Translation"
    ctl.reconfigure_services.assert_called_once()


def test_status_callback_from_worker_runs_on_gui_thread(app, cfg):
    import threading
    import time
    from unittest.mock import MagicMock

    from src.ui.widgets.main_window import MainWindow

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    ctl.get_status.return_value = {"running": True}
    win = MainWindow(ctl)
    seen = []
    win.status_manager.set_status = lambda m: seen.append(
        (m, threading.current_thread() is threading.main_thread())
    )
    win.status_manager.log_info = lambda m: None

    def toggle():
        win.handle_status_update("from worker")
        return True

    ctl.toggle_pipeline = toggle
    win.ui_controller = MagicMock()
    win.ui_controller.toggle_pipeline = toggle
    win.ui_controller.get_status.return_value = {"running": True}
    win.toggle_translation()
    for _ in range(100):
        app.processEvents()
        if win.translate_button.isEnabled():
            break
        time.sleep(0.02)
    assert ("from worker", True) in seen


def test_overlapping_exclusive_tasks_dropped(app, cfg):
    from unittest.mock import MagicMock

    from src.ui.widgets.main_window import MainWindow

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    win = MainWindow(ctl)
    win._busy = True
    assert win._run_async(lambda: None, lambda r: None) is False
