"""Edge WS stream client (spec 2026-10-31 §3/§3a): ring buffer while the
stream opens, 100 ms frames, failover with replay from the last committed
word end and dedupe, model load on model_not_ready, stop -> done."""

import json
import queue
import threading
import time

import numpy as np

from src.clients import Credential, Endpoint, EndpointPool
from src.clients.stream import FRAME, RATE, StreamClient, ws_url


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Closed(Exception):
    def __init__(self, code):
        super().__init__(f"closed {code}")
        self.rcvd = type("Close", (), {"code": code})()


class FakeWs:
    """One connection to a FakeServer: records what the client sends."""

    def __init__(self, server, url, headers):
        self.server, self.url, self.headers = server, url, headers
        self.inbox = queue.Queue()
        self.start = None
        self.audio = bytearray()
        self.closed = False

    def push(self, msg):
        self.inbox.put(msg)

    def send(self, data):
        if isinstance(data, bytes):
            self.audio += data
            self.server.on_audio(self, data)
            return
        msg = json.loads(data)
        if msg["type"] == "start":
            self.start = msg
            self.server.on_start(self)
        elif msg["type"] == "stop":
            self.server.on_stop(self)

    def recv(self, timeout=None):
        try:
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError()
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    def close(self):
        self.closed = True


class FakeServer:
    def __init__(self, ready_gate=None, errors=()):
        self.connections = []
        self.ready_gate = ready_gate
        self.errors = list(errors)  # error messages answered to start, in order
        self.finals_on_stop = []

    def connect(self, url, headers):
        ws = FakeWs(self, url, headers)
        self.connections.append(ws)
        return ws

    def on_start(self, ws):
        if self.errors:
            ws.push(self.errors.pop(0))
            return

        def ready():
            if self.ready_gate is not None:
                self.ready_gate.wait(5)
            ws.push({"type": "ready", "session_id": "s1", "model": "m"})

        threading.Thread(target=ready, daemon=True).start()

    def on_audio(self, ws, data):
        pass

    def on_stop(self, ws):
        for f in self.finals_on_stop:
            ws.push(f)
        ws.push({"type": "done"})


class ModelClient:
    def __init__(self):
        self.state = "unloaded"
        self.loads = 0

    def get_status(self):
        return {"model": {"state": self.state}}

    def load_model(self, timeout_s=None):
        self.loads += 1
        self.state = "ready"


def pool(urls=("http://a", "http://b")):
    eps = [Endpoint("asr", u, u, Credential("tok")) for u in urls]
    clients = {}

    def factory(e):
        clients[e.url] = ModelClient()
        return clients[e.url]

    p = EndpointPool("asr", eps, factory)
    p.model_clients = clients
    return p


def final(words, seq=1, eos=False):
    return {
        "type": "final",
        "seq": seq,
        "text": " ".join(w for w, _, _ in words),
        "words": [{"word": w, "start": s, "end": e, "conf": 1.0} for w, s, e in words],
        "start": words[0][1],
        "end": words[-1][2],
        "eos": eos,
    }


def client(server, p=None, **kw):
    events = []
    c = StreamClient(
        "asr",
        p or pool(),
        language="uk",
        on_event=lambda kind, msg: events.append(msg),
        connect=server.connect,
        retry_s=0.05,
        **kw,
    )
    return c, events


def tone(seconds):
    return np.full(int(seconds * RATE), 1000, dtype=np.int16)


def test_ws_url():
    assert ws_url("http://h:8091/") == "ws://h:8091/v1/audio/stream"
    assert ws_url("https://h") == "wss://h/v1/audio/stream"


def test_audio_waits_in_the_ring_until_ready_then_goes_in_100ms_frames():
    gate = threading.Event()
    server = FakeServer(ready_gate=gate)
    c, events = client(server)
    c.start()
    c.feed(tone(1.0))  # captured while the stream is still opening
    assert wait_for(lambda: server.connections and server.connections[0].start)
    ws = server.connections[0]
    assert ws.headers == {"Authorization": "Token tok"}
    assert ws.start["t0"] == 0.0 and ws.start["language"] == "uk"
    assert ws.audio == b""
    gate.set()
    assert wait_for(lambda: len(ws.audio) == RATE * 2)
    ready = [e for e in events if e["type"] == "ready"]
    assert len(ready) == 1 and not ready[0]["reconnect"]
    assert ready[0]["url"] == "http://a" and ready[0]["ms"] > 0
    c.feed(tone(0.05))  # half a frame: not sent until stop
    time.sleep(0.1)
    assert len(ws.audio) == RATE * 2
    c.stop()
    assert len(ws.audio) == int(1.05 * RATE) * 2 and ws.closed
    assert c.state == "done"


def test_failover_replays_from_the_last_committed_word_and_drops_duplicates():
    server = FakeServer()
    p = pool()
    c, events = client(server, p)
    c.start()
    c.feed(tone(2.0))
    assert wait_for(lambda: server.connections and len(server.connections[0].audio))
    a = server.connections[0]
    a.push(final([("раз", 0.1, 0.4), ("два", 0.5, 0.8)]))
    assert wait_for(lambda: any(e["type"] == "final" for e in events))
    a.push(Closed(1006))  # the node dies
    assert wait_for(lambda: len(server.connections) == 2)
    b = server.connections[1]
    assert b.url == "ws://b/v1/audio/stream"
    assert b.start["t0"] == 0.8
    assert wait_for(lambda: len(b.audio) >= int(1.2 * RATE) // FRAME * FRAME * 2)
    # the new server re-recognizes the replayed audio: "два" is a duplicate
    b.push(final([("два", 0.55, 0.8), ("три", 0.9, 1.3)], eos=True))
    assert wait_for(lambda: sum(e["type"] == "final" for e in events) == 2)
    second = [e for e in events if e["type"] == "final"][1]
    assert second["text"] == "три" and second["start"] == 0.9
    assert [e["type"] for e in events if e["type"] in ("down", "ready")] == [
        "ready",
        "down",
        "ready",
    ]
    assert p.pick() == "http://b"  # a is marked down
    assert c.reconnects == 1
    c.stop()


def test_model_not_ready_loads_the_model_and_reopens():
    server = FakeServer(
        errors=[{"type": "error", "code": "model_not_ready", "message": "x"}]
    )
    p = pool(("http://a",))
    c, events = client(server, p)
    c.start()
    assert wait_for(lambda: any(e["type"] == "ready" for e in events))
    assert p.model_clients["http://a"].loads == 1
    assert [e for e in events if e["type"] == "ready"][0]["loaded"] is True
    assert len(server.connections) == 2 and server.connections[0].closed
    c.stop()


def test_send_queue_over_limit_is_a_break():
    gate = threading.Event()
    server = FakeServer()
    sent = threading.Event()

    def stall(ws, data):  # the link is stuck: sending blocks
        sent.set()
        gate.wait(5)

    server.on_audio = stall
    c, events = client(server, send_queue_s=1.0)
    c.start()
    c.feed(tone(0.2))
    assert sent.wait(3)
    c.feed(tone(1.5))
    gate.set()
    assert wait_for(lambda: any(e["type"] == "down" for e in events))
    down = [e for e in events if e["type"] == "down"][0]
    assert "send queue" in down["error"]
    c.close()


def test_ring_keeps_at_most_ring_s_while_opening():
    gate = threading.Event()
    server = FakeServer(ready_gate=gate)
    c, _ = client(server, ring_s=1.0)
    c.feed(tone(1.5))
    c.start()
    assert c.stats()["lost_s"] == 0.5
    gate.set()
    assert wait_for(lambda: server.connections and server.connections[0].start)
    assert server.connections[0].start["t0"] == 0.5
    assert len(c.audio(0.0, 1.5)) == RATE
    c.close()


def test_stop_delivers_the_last_finals_before_done():
    server = FakeServer()
    server.finals_on_stop = [final([("кінець", 0.2, 0.6)], eos=True)]
    c, events = client(server)
    c.start()
    c.feed(tone(0.7))
    assert wait_for(lambda: any(e["type"] == "ready" for e in events))
    c.stop()
    assert [e["text"] for e in events if e["type"] == "final"] == ["кінець"]


def test_no_endpoint_is_reported_and_retried():
    p = pool(("http://a",))
    p.mark_down("http://a")
    server = FakeServer()
    c, events = client(server, p)
    c.start()
    assert wait_for(lambda: any(e["type"] == "failed" for e in events))
    assert c.state == "down"
    c.close()
