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
from src.ui.widgets.settings_dialog import SettingsDialog  # noqa: E402

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
    dlg = SettingsDialog(s, services=[])
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
    dlg = SettingsDialog(s, services=[])
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

    dlg = SettingsDialog(current_settings(), services=[])
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

    dlg = SettingsDialog(current_settings(), services=[])
    dlg.endpoints_tab.mode.setCurrentText("translate")
    dlg.accept()
    assert "translate mode" in dlg.error_label.text()
    assert dlg.result() == 0
    assert "mode" not in (_saved(cfg).get("services") or {})


def test_managed_not_saved(app, cfg, monkeypatch):
    from src.ui.widgets.settings_dialog import current_settings

    monkeypatch.setenv("RT_EDGE_LOCAL_ASR_URL", "http://env:1")
    dlg = SettingsDialog(current_settings(), services=[])
    dlg.llm_tab.model.setCurrentText("m2")
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
    assert tab.allowed.isEnabled()  # always selectable
    keys = [
        tab.allowed.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(tab.allowed.count())
    ]
    tab.allowed.item(keys.index("tts:cyborg")).setCheckState(Qt.CheckState.Checked)
    assert sorted(tab.allowed_values()) == ["llm:cyborg", "tts:cyborg"]
    tab.allow_all.setChecked(True)
    assert tab.values()["services.allowed_services"] is None
    tab.allowed.item(0).setCheckState(
        Qt.CheckState.Unchecked
    )  # unchecking leaves "all"
    assert not tab.allow_all.isChecked()


class FakeServiceClient:
    """rt-asr / rt-tts admin API stand-in for the settings tabs."""

    def __init__(self):
        from src.services.asr.settings import AsrSettings

        self.config = {
            "schema": AsrSettings.field_schema(),
            "values": {
                "model": "whisper-small",
                "beam_size": 1,
                "vad_filter": True,
                "languages": ["pl", "uk"],
                "language": None,
            },
            "source": {"beam_size": "override"},
            "version": 3,
        }
        self.posted = []

    def get_config(self):
        return self.config

    def models(self):
        return {
            "models": [
                {"id": "whisper-small", "cached": True, "size": 5 * 10**8},
                {"id": "whisper-large-v3-turbo", "cached": False, "size": 16 * 10**8},
            ],
            "current": "whisper-small",
        }

    def update_config(self, values, version, reset=(), *, timeout_s=None):
        self.posted.append((values, version, timeout_s))
        self.config = {
            **self.config,
            "values": {**self.config["values"], **values},
            "version": version,
        }
        return {**self.config, "reloaded": "model" in values}

    def voices(self):
        return {
            "voices": [
                {"id": "af_heart", "language": "en-us", "gender": "f"},
                {"id": "am_adam", "language": "en-us", "gender": "m"},
            ],
            "default": "af_heart",
        }

    def close(self):
        pass


def _ep(node, url, local=False, kind="asr"):
    from src.clients import Credential, Endpoint

    return Endpoint(kind, node, url, Credential(""), local=local)


def test_asr_panel_lists_cached_models_and_applies_changes(app):
    from src.ui.widgets.service_tabs import AsrTab, Background

    client = FakeServiceClient()
    s = resolve_edge_settings(
        {"services": {"asr": {"prompt": "Kubernetes, NixOS"}}}, {}
    )
    tab = AsrTab(
        s,
        [_ep("cyborg", "http://127.0.0.1:8091", True)],
        lambda e: client,
        Background(sync=True),
    )
    assert tab.values() == {
        "services.asr.prompt": "Kubernetes, NixOS",
        "services.asr.previous_words": 30,
    }
    panel = tab.service
    panel.load()
    model = panel.editors["model"].widget
    labels = [model.itemText(i) for i in range(model.count())]
    assert labels[0] == "whisper-small (кеш)"
    assert labels[1].startswith("whisper-large-v3-turbo (завантажити ~1.6 GB")
    assert panel.changes() == {}
    model.setCurrentIndex(1)
    panel.editors["beam_size"].widget.setValue(5)
    langs = panel.editors["languages"].widget
    en = [langs.item(i).text() for i in range(langs.count())].index("en")
    langs.item(en).setCheckState(Qt.CheckState.Checked)
    assert panel.changes() == {
        "model": "whisper-large-v3-turbo",
        "beam_size": 5,
        "languages": ["en", "pl", "uk"],
    }
    panel.apply()
    values, version, timeout = client.posted[0]
    assert (
        version == 4 and timeout >= 600 and values["model"] == "whisper-large-v3-turbo"
    )
    assert "застосовано (модель перезавантажено)" in panel.status.text()
    assert panel.changes() == {}  # reloaded from the service


def test_tts_voice_picker_from_service(app):
    from src.ui.widgets.service_tabs import Background, TtsTab

    client = FakeServiceClient()
    s = resolve_edge_settings({"services": {"tts": {"voice": "bf_emma"}}}, {})
    tab = TtsTab(
        s,
        [_ep("cyborg", "http://127.0.0.1:8093", True, "tts")],
        lambda e: client,
        Background(sync=True),
    )
    assert tab.values() == {"services.tts.voice": "bf_emma", "services.tts.speed": None}
    tab.load_voices()
    ids = [tab.voice.itemData(i) for i in range(tab.voice.count())]
    assert ids == [None, "af_heart", "am_adam", "bf_emma"]  # unknown kept, marked
    tab.voice.setCurrentIndex(2)
    tab.speed_default.setChecked(False)
    tab.speed.setValue(1.25)
    assert tab.values() == {"services.tts.voice": "am_adam", "services.tts.speed": 1.25}


def test_llm_tab_prompt_template_and_reset(app):
    from src.core.runtime import DEFAULT_SYSTEM_PROMPT
    from src.ui.widgets.service_tabs import LlmTab

    s = resolve_edge_settings(
        {
            "services": {
                "llm": {
                    "system_prompt": "Translate {{source_lang}}.",
                    "user_template": "T: {{text}}",
                }
            }
        },
        {},
    )
    tab = LlmTab(s)
    v = tab.values()
    assert v["services.llm.system_prompt"] == "Translate {{source_lang}}."
    assert v["services.llm.user_template"] == "T: {{text}}"
    tab.reset_prompts()
    assert tab.values()["services.llm.system_prompt"] == DEFAULT_SYSTEM_PROMPT
    assert tab.values()["services.llm.user_template"] == "{{text}}"


def test_dialog_saves_per_service_settings(app, cfg):
    from src.ui.widgets.settings_dialog import current_settings

    dlg = SettingsDialog(current_settings(), services=[])
    dlg.asr_tab.prompt.setPlainText("NixOS, Kubernetes")
    dlg.llm_tab.user_template.setPlainText("Переклади: {{text}}")
    got = []
    dlg.settings_changed.connect(got.append)
    dlg.accept()
    assert got[0].asr.prompt == "NixOS, Kubernetes"
    assert got[0].llm.user_template == "Переклади: {{text}}"
    saved = _saved(cfg)["services"]
    assert saved["asr"]["prompt"] == "NixOS, Kubernetes"


def test_dialog_rejects_template_without_text(app, cfg):
    from src.ui.widgets.settings_dialog import current_settings

    dlg = SettingsDialog(current_settings(), services=[])
    dlg.llm_tab.user_template.setPlainText("no placeholder")
    dlg.accept()
    assert "must contain {{text}}" in dlg.error_label.text() and dlg.result() == 0


def test_use_choice_lists_local_via_loopback(app):
    from src.clients import ServiceEntry
    from src.ui.widgets.settings_dialog import EndpointsTab

    s = resolve_edge_settings(
        {
            "services": {
                "local": {"node": "sktop", "asr": None},
                "use": {"tts": "cyborg"},
            }
        },
        {},
    )
    services = [
        ServiceEntry("asr", "sktop", "http://100.64.0.2:8091", "t"),
        ServiceEntry("asr", "cyborg", "http://100.64.0.1:8091", "t"),
        ServiceEntry("tts", "cyborg", "http://100.64.0.1:8093", "t"),
    ]
    tab = EndpointsTab(s, services=services)
    asr = tab.use["asr"]
    assert [asr.itemData(i) for i in range(asr.count())] == [None, "sktop", "cyborg"]
    assert "127.0.0.1:8091" in asr.itemText(1) and "локальний" in asr.itemText(1)
    assert tab.use["tts"].currentData() == "cyborg"
    asr.setCurrentIndex(2)
    v = tab.values()
    assert v["services.use.asr"] == "cyborg" and v["services.use.tts"] == "cyborg"
    assert "services.use.llm" not in v  # chosen in the Переклад tab


def test_status_panel_use_combo_emits_choice(app):
    panel = ServiceStatusPanel()
    got = []
    panel.use_changed.connect(lambda k, n: got.append((k, n)))
    status = {
        "use": {"asr": None, "tts": "cyborg", "llm": None},
        "choices": {
            "asr": [{"node": "sktop", "url": "http://127.0.0.1:8091", "local": True}],
            "tts": [
                {"node": "cyborg", "url": "http://100.64.0.1:8093", "local": False}
            ],
            "llm": [],
        },
        "health": {},
    }
    panel.update_status(status)
    assert panel._use["tts"].currentData() == "cyborg"
    box = panel._use["asr"]
    assert box.itemText(1) == "sktop (http://127.0.0.1:8091), локальний"
    box.setCurrentIndex(1)
    box.activated.emit(1)
    assert got == [("asr", "sktop")]
    panel.update_status(status)  # same choices: selection not reset
    assert box.currentIndex() == 1


def test_apply_use_persists_and_validates(app, cfg):
    from src.ui.widgets.settings_dialog import apply_use, save_values

    settings, errors = apply_use("asr", "cyborg")
    assert not errors and settings.use.asr == "cyborg"
    assert _saved(cfg)["services"]["use"]["asr"] == "cyborg"
    save_values({"services.allowed_services": ["asr:sktop"]})
    settings, errors = apply_use("asr", "cyborg")
    assert settings is None and "not an allowed service" in errors[0]


def test_llm_tab_preview_and_words_per_request(app):
    from src.ui.widgets.service_tabs import LlmTab

    s = resolve_edge_settings(
        {
            "services": {
                "llm": {"user_template": "[{{source_lang}}->{{target_lang}}] {{text}}"}
            }
        },
        {},
    )
    tab = LlmTab(s)
    assert tab.words_per_request.value() == 40
    text = tab.preview.toPlainText()
    assert "[system] You are a professional interpreter" in text and "Polish" in text
    assert (
        "[запит 1/1, {{text}} = 16 слів] [Polish->English] Dzień dobry wszystkim."
        in text
    )
    tab.words_per_request.setValue(3)
    lines = tab.preview.toPlainText().splitlines()
    assert lines[1] == (
        "[запит 1/6, {{text}} = 3 слів] [Polish->English] Dzień dobry wszystkim."
    )
    assert tab.values()["services.llm.words_per_request"] == 3
    tab.user_template.setPlainText("no placeholder")
    assert "must contain {{text}}" in tab.preview.toPlainText()


def test_llm_tab_shows_legacy_template_notice(app):
    from src.ui.widgets.service_tabs import LlmTab

    s = resolve_edge_settings({"services": {"llm": {"user_template": "T: {text}"}}}, {})
    assert s.llm.user_template == "{{text}}"  # default used
    tab = LlmTab(s)
    assert not tab.notice.isHidden()
    assert "old {lang}/{text} syntax" in tab.notice.text()


def test_llm_remote_worker_selectable_with_its_models(app, cfg):
    """Owner bug 2026-10-31: the translation model/worker choice was greyed
    out (HM always exported RT_EDGE_LLM_MODEL). Without env it is editable,
    remote workers are listed and their models come from that worker."""
    from src.ui.widgets.service_tabs import Background, LlmTab
    from src.ui.widgets.settings_dialog import current_settings

    s = current_settings()
    assert "llm.model" not in s.managed
    asked = []

    class Worker:
        def __init__(self, e):
            self.e = e

        def models(self):
            asked.append((self.e.node, self.e.credential.value))
            return {"data": [{"id": "Qwen3.5-4B.Q4_K_M.gguf"}]}

        def close(self):
            pass

    from src.clients import Credential, Endpoint

    eps = [
        Endpoint(
            "llm", "cyborg", "http://127.0.0.1:8080", Credential("t-l"), local=True
        ),
        Endpoint(
            "llm", "SKTOP-NOTE27-NIXOS", "http://100.87.250.83:8080", Credential("t-r")
        ),
    ]
    tab = LlmTab(s, (), eps, Worker, Background(sync=True))
    assert tab.model.isEnabled() and tab.use.isEnabled()
    assert tab.models_button.isEnabled()
    assert [tab.use.itemData(i) for i in range(tab.use.count())] == [
        None,
        "cyborg",
        "SKTOP-NOTE27-NIXOS",
    ]
    tab.use.setCurrentIndex(2)
    tab.load_models()
    assert asked == [("SKTOP-NOTE27-NIXOS", "t-r")]
    tab.model.setCurrentText("Qwen3.5-4B.Q4_K_M.gguf")
    v = tab.values()
    assert v["services.use.llm"] == "SKTOP-NOTE27-NIXOS"
    assert v["services.llm.model"] == "Qwen3.5-4B.Q4_K_M.gguf"


def test_llm_model_read_only_only_when_set_by_env(app):
    from src.ui.widgets.service_tabs import LlmTab

    s = resolve_edge_settings({}, {"RT_EDGE_LLM_MODEL": "m"})
    tab = LlmTab(s)
    assert not tab.model.isEnabled() and tab.use.isEnabled()


def test_asr_tab_template_previous_words_and_preview(app):
    from src.ui.widgets.service_tabs import AsrTab, Background

    s = resolve_edge_settings({"services": {"source_language": "uk"}}, {})
    assert s.asr.prompt == "{{previous_text}}" and s.asr.previous_words == 30
    tab = AsrTab(s, [], lambda e: None, Background(sync=True))
    tab.previous_words.setValue(3)
    assert tab.preview.toPlainText() == (
        "сервісів розпізнавання мовлення\n[у {{previous_text}} підставлено 3 слів]"
    )
    tab.prompt.setPlainText("NixOS, Kubernetes ({{source_lang}}). {{previous_text}}")
    assert tab.preview.toPlainText().splitlines()[0] == (
        "NixOS, Kubernetes (Ukrainian). сервісів розпізнавання мовлення"
    )
    tab.prompt.setPlainText("Тільки словник")  # no variables: sent as-is
    assert tab.preview.toPlainText() == (
        "Тільки словник\n[у {{previous_text}} підставлено 0 слів]"
    )
    tab.prompt.setPlainText("{{previous}}")
    assert "unknown placeholder(s) previous" in tab.preview.toPlainText()
    tab.prompt.setPlainText("")
    assert tab.preview.toPlainText().startswith("(без prompt)")
    assert tab.values() == {
        "services.asr.prompt": "",
        "services.asr.previous_words": 3,
    }


def test_node_editor_controls_follow_schema(app):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QListWidget, QSpinBox

    from src.ui.widgets.service_tabs import Background, ServiceConfigPanel

    client = FakeServiceClient()
    panel = ServiceConfigPanel(
        "asr",
        [_ep("cyborg", "http://127.0.0.1:8091", True)],
        lambda e: client,
        Background(sync=True),
    )
    panel.load()
    ed = panel.editors
    assert isinstance(ed["model"].widget, QComboBox)
    assert isinstance(ed["language"].widget, QComboBox)
    assert ed["language"].widget.currentText() == "auto / не задано"
    assert ed["language"].read() is None
    assert isinstance(ed["languages"].widget, QListWidget)
    assert isinstance(ed["vad_filter"].widget, QCheckBox)
    beam = ed["beam_size"].widget
    assert isinstance(beam, QSpinBox) and (beam.minimum(), beam.maximum()) == (1, 10)
    model = ed["model"].widget
    ids = [model.itemData(i) for i in range(model.count())]
    assert "whisper-medium" in ids and "whisper-tiny" in ids  # full catalog
    lang = ed["language"].widget
    lang.setCurrentIndex(lang.findData("uk"))
    assert panel.changes() == {"language": "uk"}
    assert "схему" not in panel.status.text()


def test_node_editor_without_schema_says_so(app):
    from src.ui.widgets.service_tabs import Background, ServiceConfigPanel

    client = FakeServiceClient()
    del client.config["schema"]
    panel = ServiceConfigPanel(
        "asr",
        [_ep("cyborg", "http://127.0.0.1:8091", True)],
        lambda e: client,
        Background(sync=True),
    )
    panel.load()
    assert "не публікує схему" in panel.status.text()
