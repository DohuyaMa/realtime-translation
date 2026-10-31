import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.runtime import EdgeSettings, resolve_edge_settings  # noqa: E402
from src.ui.widgets.service_status_panel import (  # noqa: E402
    SERVICES,
    ServiceStatusPanel,
)
from src.ui.widgets.settings_dialog import EndpointsDialog  # noqa: E402

_WINDOWS = []


def _window(ctl):
    from src.ui.widgets.main_window import MainWindow

    win = MainWindow(ctl)
    _WINDOWS.append(win)
    return win


@pytest.fixture(scope="module")
def app():
    qapp = QApplication.instance() or QApplication([])
    yield qapp
    # Close the windows (stops their status-polling threads) and collect the
    # widgets while Qt is alive: collecting PySide wrappers at interpreter
    # shutdown can segfault.
    import gc

    while _WINDOWS:
        win = _WINDOWS.pop()
        win.minimize_to_tray.setChecked(False)
        win.close()
    qapp.processEvents()
    gc.collect()


def test_services_labels():
    assert SERVICES == {
        "asr": "ASR (rt-asr)",
        "llm": "Переклад (LLM)",
        "tts": "TTS (rt-tts)",
    }


def test_status_panel_shows_serving_nodes(app):
    panel = ServiceStatusPanel()
    panel.update_status(
        {
            "discovery": "cache",
            "active": {
                "asr": {"url": "http://127.0.0.1:8091", "node": "sktop", "local": True},
                "tts": None,
            },
            "session": {
                "active": "http://cyborg:8080",
                "node": "cyborg",
                "session_id": "s1",
                "local": False,
                "down": ["http://127.0.0.1:8080"],
            },
            "health": {
                "asr": [{"ready": True}],
                "tts": [],
                "llm": [{"ready": True, "model": "m"}],
            },
        }
    )
    detail = panel._details["llm"].text()
    assert "cyborg" in detail and "s1" in detail and "127.0.0.1:8080" in detail
    assert panel._texts["llm"].text() == "сесія"
    assert panel._texts["asr"].text() == "1/1 готові"
    asr = panel._details["asr"].text()
    assert "sktop" in asr and "локальний" in asr and "кеш" in asr


def test_endpoints_dialog_values_and_segmentation(app):
    s = resolve_edge_settings({"services": {"local": {"tts": "http://t:2"}}}, {})
    dlg = EndpointsDialog(s)
    dlg.endpoints_tab.django_urls.setPlainText("http://a:1\nhttp://b:2")
    dlg.endpoints_tab.local["asr"].setText("http://x:1")
    dlg.segmentation_tab.chunk_min_s.setValue(120)
    dlg.segmentation_tab.end_silence_ms.setValue(400)
    v = dlg.values()
    assert v["services.django_urls"] == ["http://a:1", "http://b:2"]
    assert v["services.local.asr"] == "http://x:1"
    assert v["services.local.tts"] == "http://t:2"
    assert v["services.allowed_services"] is None
    assert v["segmentation.transcribe.chunk_min_s"] == 120
    assert v["segmentation.translate.end_silence_ms"] == 400
    assert v["services.mode"] == "auto"


def test_managed_fields_disabled(app):
    s = resolve_edge_settings({}, {"RT_EDGE_LOCAL_ASR_URL": "http://e:1"})
    dlg = EndpointsDialog(s)
    assert not dlg.endpoints_tab.local["asr"].isEnabled()
    assert isinstance(s, EdgeSettings)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from src.core import config as cfgmod
    from src.core import runtime

    for k in list(os.environ):
        if k.startswith("RT_EDGE_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
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
    dlg.endpoints_tab.local["asr"].setText("http://a:1")
    dlg.segmentation_tab.end_silence_off.setChecked(True)
    dlg.accept()
    assert got and got[0].local.asr == "http://a:1"
    assert got[0].segmentation.translate.end_silence_ms is None
    assert _saved(cfg)["services"]["local"]["asr"] == "http://a:1"


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

    monkeypatch.setenv("RT_EDGE_LOCAL_ASR_URL", "http://env:1")
    dlg = EndpointsDialog(current_settings())
    dlg.endpoints_tab.model.setText("m2")
    dlg.accept()
    saved = _saved(cfg)["services"]
    assert "asr" not in (saved.get("local") or {})
    assert saved["llm"]["model"] == "m2"


def test_main_window_constructs(app, cfg):
    import time
    from unittest.mock import MagicMock

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    ctl.get_status.return_value = {"running": True}
    win = _window(ctl)
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

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    ctl.get_status.return_value = {"running": True}
    win = _window(ctl)
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

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    win = _window(ctl)
    win._busy = True
    assert win._run_async(lambda: None, lambda r: None) is False


def test_status_panel_marks_offline_local_worker(app):
    panel = ServiceStatusPanel()
    panel.update_status(
        {
            "discovery": "none",
            "session": {
                "active": "http://127.0.0.1:8080",
                "node": "sktop",
                "local": True,
                "offline": True,
            },
            "health": {},
        }
    )
    assert "офлайн-токен" in panel._details["llm"].text()


def test_allowed_services_selection(app):
    from src.clients import ServiceEntry

    s = resolve_edge_settings({"services": {"allowed_services": ["llm:cyborg"]}}, {})
    from src.ui.widgets.settings_dialog import EndpointsTab

    tab = EndpointsTab(
        s,
        services=[
            ServiceEntry("llm", "cyborg", "http://c:8080", "t"),
            ServiceEntry("tts", "cyborg", "http://c:8093", "t"),
        ],
    )
    assert not tab.allow_all.isChecked()
    assert tab.allowed_values() == ["llm:cyborg"]
    tab.allowed.item(1).setCheckState(Qt.CheckState.Checked)
    assert tab.allowed_values() == ["llm:cyborg", "tts:cyborg"]
    tab.allow_all.setChecked(True)
    assert tab.values()["services.allowed_services"] is None
