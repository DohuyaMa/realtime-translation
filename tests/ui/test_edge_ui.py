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


def test_main_window_constructs(app):
    from unittest.mock import MagicMock

    from src.ui.widgets.main_window import MainWindow

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    ctl.get_status.return_value = {}
    win = MainWindow(ctl)
    assert win.service_status_panel.get_service_names() == ["asr", "llm", "tts"]
    win._apply_ui_update({"input": 0.5, "health": {}})
    win.on_general_settings_changed(resolve_edge_settings({}, {}))
    ctl.reconfigure_services.assert_called_once()
