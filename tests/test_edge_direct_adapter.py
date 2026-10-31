import dataclasses

import pytest

from src.adapters import direct_adapter as da
from src.adapters.direct_adapter import DirectAdapter
from src.clients import WorkerSessions
from src.core.runtime import EdgeSettings, LocalUrls

VALID = EdgeSettings(
    mode="translate",
    local=LocalUrls(llm="http://m", tts="http://t"),
)


class FakeTS:
    instances = []

    def __init__(self, settings, **kw):
        self.settings = settings
        self.started = False
        self.is_running = False
        self.cleaned = False
        self.applied = []
        self.stops = 0
        self.last_error = None
        self.start_ok = True
        self.rebuild = False
        self.pools = object()
        self.llm = object()
        self.llm_target = None
        self._audio_level_input = 0.25
        FakeTS.instances.append(self)

    def start(self):
        self.started = self.is_running = self.start_ok
        if not self.start_ok:
            self.last_error = "llm: no llm worker available"
        return self.start_ok

    def stop(self):
        self.stops += 1
        self.started = self.is_running = False

    def apply_settings(self, settings):
        self.applied.append(settings)
        self.settings = settings
        if self.rebuild:
            self.pools = object()
            if self.started:
                self.start()

    def get_stats(self):
        return {"running": self.started, "session": {"node": "w"}}

    def set_devices(self, i, o):
        self.devices = (i, o)

    def cleanup(self):
        self.cleaned = True


class FakeMonitor:
    def __init__(self, pools, llm, sessions):
        self.args = (pools, llm, sessions)
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def poll_once(self):
        return {"asr": [{"connected": True}]}


class FakeRouter:
    def __init__(self):
        self.calls = []

    def set_default_source(self, n):
        self.calls.append(("src", n))

    def set_default_sink(self, n):
        self.calls.append(("sink", n))

    def list_devices(self):
        return {"inputs": [{"name": "m", "description": "Mic"}], "outputs": []}


def adapter(settings):
    return DirectAdapter(
        skip_preflight=True,
        settings=settings,
        translation_system_factory=FakeTS,
        health_factory=FakeMonitor,
        audio_router_factory=FakeRouter,
    )


def test_start_pipeline_rejects_invalid_settings():
    a = adapter(EdgeSettings(mode="translate"))
    assert a.start_pipeline() is False
    assert "llm" in a.last_error and "tts" in a.last_error
    assert FakeTS.instances[-1].started is False


def test_start_failure_surfaces_system_error():
    a = adapter(VALID)
    FakeTS.instances[-1].start_ok = False
    assert a.start_pipeline() is False
    assert a.last_error == "llm: no llm worker available"


def test_start_and_stop_pipeline():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert a.start_pipeline() is True and a.last_error is None
    assert ts.started is True
    assert a.stop_pipeline() is True and ts.started is False


def test_health_monitor_built_from_system():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert a.refresh_services() == {"asr": [{"connected": True}]}
    assert a._monitor.args == (ts.pools, ts.llm, None)
    # backends rebuilt by apply_settings -> monitor follows the new objects
    ts.pools = object()
    a.refresh_services()
    assert a._monitor.args[0] is ts.pools


def test_reconfigure_calls_apply_settings():
    a = adapter(EdgeSettings())
    ts = FakeTS.instances[-1]
    assert a.reconfigure_services(VALID) is True
    assert ts.applied == [VALID] and a.settings == VALID
    assert a.translation_system is ts


def test_reconfigure_segmentation_does_not_restart_pipeline():
    a = adapter(VALID)
    a.start_pipeline()
    ts = FakeTS.instances[-1]
    count = len(FakeTS.instances)
    seg = dataclasses.replace(
        VALID.segmentation,
        translate=dataclasses.replace(VALID.segmentation.translate, min_speech_ms=500),
    )
    new = dataclasses.replace(VALID, segmentation=seg)
    assert a.reconfigure_services(new) is True
    assert ts.applied == [new]
    assert ts.stops == 0 and ts.started is True and ts.cleaned is False
    assert len(FakeTS.instances) == count


def test_reconfigure_with_invalid_settings_keeps_old():
    a = adapter(EdgeSettings())
    ts = FakeTS.instances[-1]
    assert a.reconfigure_services(EdgeSettings(mode="translate")) is False
    assert ts.applied == [] and a.settings == EdgeSettings()
    assert "llm" in a.last_error


def test_status_exposes_session_and_levels():
    a = adapter(VALID)
    assert a.get_status()["session"] == {"node": "w"}
    assert a.get_audio_levels() == {"input": 0.25, "output": 0.0}


def test_set_languages_updates_source_language():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert a.set_languages("pl", "en") is True
    assert ts.applied[-1].source_language == "pl"


def test_devices():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert [d.name for d in a.get_input_devices()] == ["m"]
    assert a.set_input_device("m") and a.set_output_device("o")
    assert ts.devices == ("m", "o")
    assert ("src", "m") in a.audio_router.calls


def test_no_service_management_left():
    for name in (
        "start_service",
        "stop_service",
        "restart_service",
        "restart_all_services",
        "change_whisper_model",
        "reconfigure_wyoming",
        "reconnect_ipc",
        "get_service_active",
    ):
        assert not hasattr(DirectAdapter, name), name


def test_preflight_failure_mentions_home_manager(monkeypatch):
    monkeypatch.setattr(da.PipeWirePreflight, "check", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="virtualSinks"):
        DirectAdapter(settings=VALID, translation_system_factory=FakeTS)


def test_monitor_started_and_stopped_with_pipeline():
    a = adapter(VALID)
    a.start_pipeline()
    assert a._monitor.running is True
    a.stop_pipeline()
    assert a._monitor.running is False


def test_monitor_stops_even_if_stop_raises():
    a = adapter(VALID)
    a.start_pipeline()
    a.translation_system.stop = lambda: 1 / 0
    assert a.stop_pipeline() is False
    assert a._monitor.running is False


def test_worker_sessions_target_passed_to_monitor():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    ts.llm_target = object.__new__(WorkerSessions)
    a.refresh_services()
    assert a._monitor.args[2] is ts.llm_target


def test_monitor_follows_rebuild_while_running():
    a = adapter(VALID)
    a.start_pipeline()
    ts = FakeTS.instances[-1]
    old = a._monitor
    ts.rebuild = True
    assert a.reconfigure_services(VALID) is True
    assert a._monitor is not old and old.running is False
    assert a._monitor.running is True and a._monitor.args[0] is ts.pools


def test_reconfigure_failed_restart_reports_error():
    a = adapter(VALID)
    a.start_pipeline()
    ts = FakeTS.instances[-1]
    ts.rebuild = True
    ts.start_ok = False
    assert a.reconfigure_services(VALID) is False
    assert a.last_error == "llm: no llm worker available"
    assert a._monitor.running is False
