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
    dlg.llm_tab.end_silence_ms.setValue(400)
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
    dlg.llm_tab.end_silence_off.setChecked(True)
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
        self.actions = []
        self.model_state = {"id": "whisper-small", "state": "ready", "error": None}

    def get_config(self):
        return self.config

    def get_status(self):
        return {
            "ready": self.model_state["state"] == "ready",
            "model": dict(self.model_state, device="cpu"),
            "memory": {"rss_mb": 2800, "swap_mb": 5700, "vram_mb": None},
        }

    def load_model(self, *, timeout_s=None):
        self.actions.append("load")
        self.model_state = dict(
            self.model_state,
            state="downloading",
            download={"done_mb": 1, "total_mb": 4},
        )
        return {"state": "downloading"}

    def unload_model(self, *, timeout_s=None):
        self.actions.append("unload")
        self.model_state = dict(self.model_state, state="unloaded")
        return {"state": "unloaded"}

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
    assert lines[2] == (
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


def test_llm_tab_pause_setting(app):
    from src.ui.widgets.service_tabs import LlmTab

    tab = LlmTab(resolve_edge_settings({}, {}))
    assert tab.end_silence_ms.value() == 700
    assert (tab.end_silence_ms.minimum(), tab.end_silence_ms.maximum()) == (200, 3000)
    tab.end_silence_ms.setValue(450)
    assert "пауза 450 мс" in tab.preview.toPlainText()
    assert tab.values()["segmentation.translate.end_silence_ms"] == 450


def test_status_panel_shows_stage_timing(app):
    panel = ServiceStatusPanel()
    panel.update_status(
        {
            "timing": {
                "seq": 7,
                "asr_ms": 3800.0,
                "llm_ms": 400.0,
                "llm_total_ms": 900.0,
                "tts_ms": 4700.0,
                "e2e_ms": 8900.0,
            },
            "queues": {"asr": 0, "llm": 1, "tts": 2, "playback": 0},
            "backpressure": {"llm": 0, "tts": 3, "playback": 0},
            "dropped_segments": 0,
            "health": {},
        }
    )
    text = panel._timing.text()
    assert "Фраза #7: ASR 3800 мс · LLM 400 мс (усього 900) · TTS 4700 мс" in text
    assert "кінець мовлення → звук 8900 мс" in text
    assert "черги: llm 1, tts 2" in text and "не встигає: tts ×3" in text


_TABS = []  # keep the tabs alive: the panel's widgets die with them


def _asr_panel(client):
    from src.ui.widgets.service_tabs import AsrTab, Background

    tab = AsrTab(
        resolve_edge_settings({}, {}),
        [_ep("cyborg", "http://127.0.0.1:8091", True)],
        lambda e: client,
        Background(sync=True),
    )
    _TABS.append(tab)
    return tab.service


def test_service_panel_shows_actual_model_not_configured(app):
    client = FakeServiceClient()
    # owner picked large-v3, the service still runs turbo after a failed load
    client.config["values"]["model"] = "whisper-large-v3"
    client.model_state = {
        "id": "whisper-large-v3-turbo",
        "state": "error",
        "error": "CUDA failed with error out of memory",
    }
    panel = _asr_panel(client)
    assert not panel.load_model_button.isEnabled()  # state unknown yet
    panel.load()
    text = panel.model_state.text()
    assert "<b>whisper-large-v3-turbo</b>" in text
    assert "CUDA failed with error out of memory" in text and "#c00000" in text
    assert "у налаштуваннях: whisper-large-v3, але фактично" in text
    assert panel.memory.text() == "RAM 2.7 ГБ · swap 5.6 ГБ · VRAM немає"
    # error: both explicit actions available
    assert panel.load_model_button.isEnabled()
    assert panel.unload_model_button.isEnabled()


def test_service_panel_load_unload_buttons_and_polling(app):
    client = FakeServiceClient()
    panel = _asr_panel(client)
    panel.load()
    assert panel.unload_model_button.isEnabled()
    assert not panel.load_model_button.isEnabled()  # ready already
    panel.unload_model()
    assert client.actions == ["unload"]
    assert "не завантажена" in panel.model_state.text()
    assert panel.load_model_button.isEnabled()
    assert not panel.poll_timer.isActive()
    panel.load_model()
    assert client.actions == ["unload", "load"]
    # downloading: progress shown, everything disabled, /status polled
    assert "завантажується з мережі 1/4 МБ (25%)" in panel.model_state.text()
    assert panel.busy() and panel.poll_timer.isActive()
    for b in (panel.load_model_button, panel.unload_model_button, panel.apply_button):
        assert not b.isEnabled()
    client.model_state = {"id": "whisper-small", "state": "ready", "error": None}
    panel.refresh_state()  # the timer's tick
    assert "готова (cpu)" in panel.model_state.text()
    assert not panel.poll_timer.isActive() and panel.apply_button.isEnabled()


def test_service_panel_status_failure_is_shown(app):
    client = FakeServiceClient()

    def boom():
        raise RuntimeError("asr: unreachable")

    client.get_status = boom
    panel = _asr_panel(client)
    panel.load()
    assert "стан моделі недоступний: asr: unreachable" in panel.model_state.text()
    assert not panel.load_model_button.isEnabled()


def test_status_panel_per_instance_model_state_and_counters(app):
    panel = ServiceStatusPanel()
    panel.update_status(
        {
            "choices": {
                "asr": [{"node": "cyborg", "url": "http://c:8091", "local": False}]
            },
            "health": {
                "asr": [
                    {
                        "url": "http://c:8091",
                        "connected": True,
                        "ready": False,
                        "model": "whisper-distil-large-v3.5",
                        "state": "error",
                        "model_error": "CUDA out of memory",
                        "memory": {"rss_mb": 2800, "swap_mb": 5700, "vram_mb": 1600},
                        "dropped": {"hallucination": 4},
                    },
                    {"url": "http://x:8091", "connected": False, "error": "timeout"},
                ],
            },
            "llm_rejected": 2,
            "asr_no_words": 5,
            "playback_short": 1,
        }
    )
    asr = panel._details["asr"].text()
    assert "cyborg: модель: <b>whisper-distil-large-v3.5</b>" in asr
    assert "помилка: CUDA out of memory" in asr
    assert "VRAM 1.6 ГБ" in asr and "відкинуто сервісом: hallucination 4" in asr
    assert "http://x:8091" in asr and "недоступний (timeout)" in asr
    stats = panel._timing.text()
    assert "відкинуто LLM-відповідей (не переклад): 2" in stats
    assert "ASR без слів: 5" in stats
    assert "відтворень, коротших за аудіо: 1" in stats


def test_pipeline_events_described_and_deduplicated():
    from src.ui.controller.ui_controller import PipelineEventLog

    now = [0.0]
    log = PipelineEventLog(clock=lambda: now[0])
    rejected = {
        "status": "translation_rejected",
        "reason": "length",
        "original_text": "Dzień dobry",
        "output": "I don't see any transcript",
    }
    level, msg = log.lines(rejected)
    assert level == "WARN"
    assert msg == (
        "Переклад відкинуто (length): «Dzień dobry» → «I don't see any transcript»"
    )
    err = {"status": "error", "service": "asr", "kind": "not_ready", "error": "x"}
    level, msg = log.lines(err)
    assert level == "ERROR" and msg.startswith("ASR: модель не завантажена")
    assert log.lines(err) is None  # repeated within the window: counted
    now[0] = 11.0
    assert log.lines(err)[1].endswith("(ще ×1 за 10 с)")
    assert log.lines({"status": "timing", "timing": {}}) is None
    warm = {"status": "llm_warmup", "warmup": {"url": "http://w", "error": "x"}}
    assert log.lines(warm) == ("WARN", "Прогрів LLM на http://w не вдався: x")


def test_main_window_logs_pipeline_events(app, cfg):
    from unittest.mock import MagicMock

    ctl = MagicMock()
    ctl.get_input_devices.return_value = []
    ctl.get_output_devices.return_value = []
    win = _window(ctl)
    ctl.set_pipeline_event_callback.assert_called_once()
    emit = ctl.set_pipeline_event_callback.call_args[0][0]
    emit({"status": "error", "service": "tts", "kind": "not_ready"})
    app.processEvents()
    assert (
        "ERROR: TTS: модель не завантажена" in win.status_logger.log_view.toPlainText()
    )


def test_save_values_persists_only_overrides(app, cfg):
    from src.core.prompt import DEFAULT_SYSTEM_PROMPT
    from src.ui.widgets.settings_dialog import save_values

    default = resolve_edge_settings({}, {})
    hard_max = default.segmentation.translate.hard_max_ms
    # a stale copy of a default in the user file is dropped on the next save
    cfg.set("services.llm.temperature", default.llm.temperature)
    cfg.save()
    settings = save_values(
        {
            "services.llm.temperature": default.llm.temperature,
            "services.llm.system_prompt": DEFAULT_SYSTEM_PROMPT,
            "segmentation.translate.hard_max_ms": hard_max,
            "services.llm.model": "m2",
        }
    )
    assert _saved(cfg) == {"services": {"llm": {"model": "m2"}}}
    assert settings.llm.model == "m2"
    assert settings.segmentation.translate.hard_max_ms == hard_max
    save_values({"services.llm.model": default.llm.model})
    assert _saved(cfg) == {}


def test_dialog_accept_writes_no_defaults(app, cfg):
    from src.ui.widgets.settings_dialog import current_settings

    SettingsDialog(current_settings(), services=[]).accept()
    assert _saved(cfg) == {}
