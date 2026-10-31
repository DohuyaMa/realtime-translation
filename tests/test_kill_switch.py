"""Kill switch primitives of TranslationSystem, playback into a named sink,
the PipeWire preflight and the tray icon state."""

import dataclasses
import os
import subprocess

import numpy as np
import pytest

from src.clients import Credential, Directory, Endpoint, ServiceEntry, WorkerSessions
from src.core.preflight import pipewire as preflight
from src.core.runtime import EdgeSettings, LocalUrls
from src.translation_system import TranslationSystem, play_to_sinks


class CountingDirectory(Directory):
    def __post_init__(self):
        super().__post_init__()
        self.periodic = []

    def start_periodic(self, interval_s: float = 0.0) -> None:
        self.periodic.append("start")

    def stop_periodic(self) -> None:
        self.periodic.append("stop")


SETTINGS = EdgeSettings(
    mode="transcribe",
    local=LocalUrls(
        node="me", asr="http://127.0.0.1:8091", tts="http://127.0.0.1:8093"
    ),
)
CLUSTER = [
    ServiceEntry("asr", "other", "http://other:8091", "t1"),
    ServiceEntry("tts", "other", "http://other:8093", "t2"),
]


def system(settings=SETTINGS, target=None):
    dirs = []

    def directory(s):
        d = CountingDirectory(list(CLUSTER), "cache")
        dirs.append(d)
        return d

    ts = TranslationSystem(
        settings,
        directory_factory=directory,
        llm_target_factory=lambda s, d: target,
    )
    return ts, dirs


def test_local_model_clients_are_this_host_only():
    ts, _ = system()
    clients = ts.local_model_clients()
    try:
        assert {k: c.base_url for k, c in clients.items()} == {
            "asr": "http://127.0.0.1:8091",
            "tts": "http://127.0.0.1:8093",
        }
    finally:
        for c in clients.values():
            c.close()


def test_local_model_clients_even_when_pinned_to_another_node():
    use = dataclasses.replace(SETTINGS.use, asr="other")
    ts, _ = system(dataclasses.replace(SETTINGS, use=use))
    clients = ts.local_model_clients()
    try:
        assert clients["asr"].base_url == "http://127.0.0.1:8091"
    finally:
        for c in clients.values():
            c.close()


def test_paused_discovery_stays_paused_across_rebuild():
    ts, dirs = system()
    assert dirs[0].periodic == ["start"]
    ts.pause_background()
    assert dirs[0].periodic == ["start", "stop"]
    # a settings change that rebuilds the backends must not wake it up
    ts.apply_settings(dataclasses.replace(SETTINGS, allowed_services=["asr:me"]))
    assert dirs[-1] is not dirs[0] and dirs[-1].periodic == []
    ts.resume_background()
    assert dirs[-1].periodic == ["start"]


def test_release_session_stops_worker_sessions():
    ep = Endpoint("llm", "me", "http://127.0.0.1:8080", Credential("t"), local=True)
    target = WorkerSessions([ep])
    stopped = []
    target.stop = lambda: stopped.append(True)
    ts, _ = system(target=target)
    ts._session_held = True
    ts.release_session()
    assert stopped == [True] and ts._session_held is False


# -- playback into a named sink ---------------------------------------------


class FakeProc:
    procs = []

    def __init__(self, args, stdin=None, stdout=None, stderr=None, code=0, err=b""):
        self.args, self.code, self.err = args, code, err
        self.returncode = None
        self.fed = None
        FakeProc.procs.append(self)

    def communicate(self, data=None):
        self.fed = data
        self.returncode = self.code
        return b"", self.err


def test_play_to_sinks_plays_every_sink(monkeypatch):
    FakeProc.procs = []
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    audio = np.array([0.0, 0.5, -0.5], dtype=np.float64)
    play_to_sinks(audio, 24000, ["rt_virtual_output", "phones", None])
    args = [p.args for p in FakeProc.procs]
    assert all(a[0] == "pacat" and "--rate=24000" in a for a in args)
    assert [next((x for x in a if x.startswith("--device")), None) for a in args] == [
        "--device=rt_virtual_output",
        "--device=phones",
        None,
    ]
    for p in FakeProc.procs:
        assert np.frombuffer(p.fed, dtype=np.float32).tolist() == [0.0, 0.5, -0.5]


def test_play_to_sinks_failure_is_explicit(monkeypatch):
    def popen(args, **kw):
        bad = "--device=phones" in args
        return FakeProc(
            args, code=1 if bad else 0, err=b"No such entity" if bad else b""
        )

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="phones.*No such entity"):
        play_to_sinks(np.zeros(4, dtype=np.float32), 24000, ["out", "phones"])


def test_default_sink_adds_monitor_only_when_on(monkeypatch):
    import src.translation_system as tsmod

    calls = []
    monkeypatch.setattr(
        tsmod, "play_to_sinks", lambda a, sr, sinks: calls.append(sinks)
    )
    ts, _ = system()
    ts.set_devices(None, "rt_virtual_output")
    ts._default_sink(np.zeros(4, dtype=np.float32), 24000)
    ts.set_devices(None, "rt_virtual_output", "phones")
    ts._default_sink(np.zeros(4, dtype=np.float32), 24000)
    assert calls == [["rt_virtual_output"], ["rt_virtual_output", "phones"]]


# -- capture from a named source ------------------------------------------------


class FakeCapture:
    def __init__(self, source, log, fail=False):
        self.source, self.log, self.fail = source, log, fail

    def start(self):
        if self.fail:
            raise RuntimeError(f"parec from {self.source} failed")
        self.log.append(("start", self.source))

    def stop(self):
        self.log.append(("stop", self.source))

    def close(self):
        pass


def test_input_switch_while_capturing_swaps_the_stream():
    log = []
    ts, _ = system()
    ts._capture_factory = lambda on_block: FakeCapture(
        ts.input_device_name, log, fail=ts.input_device_name == "broken"
    )
    ts._stream = FakeCapture("mic1", log)
    ts._capturing = True
    ts.set_devices("mic2", None)
    assert log == [("start", "mic2"), ("stop", "mic1")]
    assert ts._stream.source == "mic2"
    with pytest.raises(RuntimeError, match="broken"):
        ts.set_devices("broken", None)
    assert ts.input_device_name == "mic2" and ts._stream.source == "mic2"


def test_input_change_while_stopped_only_records_it():
    ts, _ = system()
    ts._capture_factory = lambda on_block: pytest.fail("no capture when stopped")
    ts.set_devices("mic2", None)
    assert ts.input_device_name == "mic2"


def test_pulse_capture_reads_blocks_from_the_source(monkeypatch):
    import io

    import src.translation_system as tsmod

    blocks = []
    block = np.arange(tsmod.BLOCKSIZE, dtype=np.float32)

    class Proc:
        def __init__(self, args, stdout=None, stderr=None):
            self.args = args
            self.stdout = io.BytesIO(block.tobytes() * 2)
            self.stderr = io.BytesIO(b"")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(self.args, timeout)

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Proc)
    cap = tsmod.PulseCapture(blocks.append, "rt_virtual_input.monitor")
    cap.start()
    cap.stop()
    cap.close()
    assert "--device=rt_virtual_input.monitor" in cap._proc.args
    assert len(blocks) == 2 and blocks[0].tolist() == block.tolist()


def test_pulse_capture_unknown_source_fails_at_start(monkeypatch):
    import io

    import src.translation_system as tsmod

    class Proc:
        def __init__(self, args, stdout=None, stderr=None):
            self.stderr = io.BytesIO(b"Stream error: No such entity")

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(subprocess, "Popen", Proc)
    with pytest.raises(RuntimeError, match="nope.*No such entity"):
        tsmod.PulseCapture(lambda b: None, "nope").start()


# -- preflight --------------------------------------------------------------


def _pactl(sinks, sources):
    def out(args, text=True):
        names = sinks if args[2] == "sinks" else sources
        return "".join(
            f"{i}\t{n}\tPipeWire\tfloat32le\tIDLE\n" for i, n in enumerate(names)
        )

    return out


def test_preflight_requires_the_virtual_mic(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda n: "/bin/pactl")
    sinks = ["rt_virtual_input", "rt_virtual_output"]
    monkeypatch.setattr(
        preflight.subprocess,
        "check_output",
        _pactl(sinks, ["rt_virtual_output.monitor"]),
    )
    assert preflight.PipeWirePreflight.check() is False
    monkeypatch.setattr(
        preflight.subprocess,
        "check_output",
        _pactl(sinks, ["rt_virtual_output.monitor", "rt_virtual_mic"]),
    )
    assert preflight.PipeWirePreflight.check() is True


# -- tray icon state ----------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from src.ui.widgets.tray_icon import app_state, render_icon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_app_state_from_kill_switch():
    s = app_state(standby=True, busy=None, translating=False)
    assert (s.base, s.badge) == ("stopped", None)
    assert "вивантажено" in s.tooltip
    s = app_state(standby=False, busy=None, translating=True)
    assert (s.base, s.badge, s.text) == ("running", None, "перекладає")
    s = app_state(standby=False, busy=None, translating=False)
    assert (s.base, s.badge) == ("idle", None)
    s = app_state(standby=False, busy="stopping", translating=True)
    assert s.badge == "loading"
    s = app_state(standby=False, busy="starting", translating=False)
    assert (s.base, s.badge) == ("stopped", "loading")
    s = app_state(standby=True, busy=None, translating=False, errors=["tts: x"])
    assert (s.base, s.badge) == ("stopped", "error") and "tts: x" in s.text


def test_icons_differ_per_state(qapp):
    def pixels(**kw):
        img = render_icon(app_state(**kw), 32).toImage()
        return bytes(img.constBits())

    stopped = pixels(standby=True, busy=None, translating=False)
    running = pixels(standby=False, busy=None, translating=True)
    loading = pixels(standby=False, busy="starting", translating=False)
    assert len({stopped, running, loading}) == 3
