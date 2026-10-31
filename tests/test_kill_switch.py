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
from src.translation_system import TranslationSystem, play_to_sink


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


def test_play_to_sink_targets_the_sink(monkeypatch):
    calls = []

    def run(args, input=None, capture_output=False):
        calls.append((args, input))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    audio = np.array([0.0, 0.5, -0.5], dtype=np.float64)
    play_to_sink(audio, 24000, "rt_virtual_output")
    args, data = calls[0]
    assert args[0] == "pacat" and "--device=rt_virtual_output" in args
    assert "--rate=24000" in args and "--format=float32le" in args
    assert np.frombuffer(data, dtype=np.float32).tolist() == [0.0, 0.5, -0.5]
    play_to_sink(audio, 24000, None)
    assert not [a for a in calls[1][0] if a.startswith("--device")]


def test_play_to_sink_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, b"", b"No such entity"),
    )
    with pytest.raises(RuntimeError, match="rt_virtual_output.*No such entity"):
        play_to_sink(np.zeros(4, dtype=np.float32), 24000, "rt_virtual_output")


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
