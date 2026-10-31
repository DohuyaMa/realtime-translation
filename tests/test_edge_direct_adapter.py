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

    def set_devices(self, i, o, m=None):
        if i == "broken":
            raise RuntimeError("parec from broken failed (exit 1): No such entity")
        self.devices = (i, o, m)

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
    assert a.set_monitor_device("phones")
    assert ts.devices == ("m", "o", "phones")
    assert a.set_monitor_device(None) and ts.devices == ("m", "o", None)


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


# -- kill switch (standby / resume) -----------------------------------------


class FakeModelClient:
    def __init__(self, kind, log, states=("ready",)):
        self.kind, self.log = kind, log
        self.base_url = f"http://127.0.0.1/{kind}"
        self.states = list(states)

    def unload_model(self, timeout_s=None):
        self.log.append(f"unload {self.kind}")

    def load_model(self, timeout_s=None):
        self.log.append(f"load {self.kind}")

    def get_status(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"model": {"id": "m", "state": state}}

    def close(self):
        pass


class PowerTS(FakeTS):
    """FakeTS that records the kill-switch steps in one shared log."""

    def __init__(self, settings, **kw):
        super().__init__(settings, **kw)
        self.log = []
        self.model_states = {"asr": ("ready",), "tts": ("ready",)}

    def start(self):
        self.log.append("start pipeline")
        return super().start()

    def stop(self):
        if self.is_running:
            self.log.append("stop pipeline")
        super().stop()

    def release_session(self):
        self.log.append("release session")

    def pause_background(self):
        self.log.append("pause discovery")

    def resume_background(self):
        self.log.append("resume discovery")

    def local_model_clients(self):
        return {
            k: FakeModelClient(k, self.log, self.model_states[k])
            for k in ("asr", "tts")
        }


def power_adapter():
    a = DirectAdapter(
        skip_preflight=True,
        settings=VALID,
        translation_system_factory=PowerTS,
        health_factory=FakeMonitor,
        audio_router_factory=FakeRouter,
    )
    a._sleep = lambda s: None
    return a


def test_standby_order_stop_release_unload():
    a = power_adapter()
    a.start_pipeline()
    ts = a.translation_system
    ts.log.clear()
    assert a.standby() == []
    assert ts.log == [
        "stop pipeline",
        "release session",
        "pause discovery",
        "unload asr",
        "unload tts",
    ]
    assert a.in_standby is True and a._monitor.running is False


def test_standby_blocks_start_until_resume():
    a = power_adapter()
    a.standby()
    assert a.start_pipeline() is False
    assert "Запустити" in a.last_error
    assert a.translation_system.started is False


def test_resume_is_standby_in_reverse_and_restarts_pipeline():
    a = power_adapter()
    a.start_pipeline()
    ts = a.translation_system
    a.standby()
    ts.log.clear()
    assert a.resume() == []
    assert ts.log == ["load asr", "load tts", "resume discovery", "start pipeline"]
    assert a.in_standby is False and ts.started is True


def test_resume_does_not_start_pipeline_that_was_stopped():
    a = power_adapter()
    a.standby()
    ts = a.translation_system
    ts.log.clear()
    assert a.resume() == []
    assert "start pipeline" not in ts.log and ts.started is False


def test_resume_waits_for_models_ready():
    a = power_adapter()
    ts = a.translation_system
    ts.model_states = {"asr": ("loading", "loading", "ready"), "tts": ("ready",)}
    a.standby()
    sleeps = []
    a._sleep = sleeps.append
    assert a.resume() == []
    assert len(sleeps) == 2


def test_resume_reports_model_error_and_timeout():
    a = power_adapter()
    ts = a.translation_system
    ts.model_states = {"asr": ("error",), "tts": ("loading",)}
    a.standby()
    now = [0.0]
    a._clock = lambda: now[0]

    def sleep(s):
        now[0] += 1000.0

    a._sleep = sleep
    errors = a.resume()
    assert any(e.startswith("asr: model error") for e in errors)
    assert any(e.startswith("tts: model not ready") for e in errors)
    assert a.in_standby is False and a.last_error


def test_standby_failure_in_one_step_still_runs_the_rest():
    a = power_adapter()
    ts = a.translation_system

    def boom():
        raise RuntimeError("worker gone")

    ts.release_session = boom
    errors = a.standby()
    assert errors == ["worker session: worker gone"]
    assert ts.log[-2:] == ["unload asr", "unload tts"]
    assert a.last_error == "worker session: worker gone"


def test_devices_never_change_system_defaults():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert a.set_output_device("rt_virtual_output")
    assert a.set_input_device("rt_virtual_input.monitor")
    assert ts.devices == ("rt_virtual_input.monitor", "rt_virtual_output", None)
    assert not hasattr(a.audio_router, "calls") or a.audio_router.calls == []
    assert a.set_output_device("") and ts.devices[1] is None


def test_input_device_failure_keeps_old_source():
    a = adapter(VALID)
    ts = FakeTS.instances[-1]
    assert a.set_input_device("m")
    assert a.set_input_device("broken") is False
    assert "No such entity" in a.last_error
    assert a._input_device == "m" and ts.devices[0] == "m"
