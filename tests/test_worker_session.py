import json

import httpx
import pytest

from src.clients import Credential, Endpoint, LlmClient, ServiceError, WorkerSessions
from src.core.runtime import LlmSettings


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeWorkers:
    """Per-host worker: app-session API + chat; host missing = down."""

    def __init__(self):
        self.up = {}  # host -> {"token": str, "sessions": set}
        self.calls = []
        self.n = 0

    def add(self, host, token="t"):
        self.up[host] = {"token": token, "sessions": set()}

    def __call__(self, req):
        host, path, method = req.url.host, req.url.path, req.method
        self.calls.append((host, method, path, req.headers.get("authorization")))
        w = self.up.get(host)
        if w is None:
            raise httpx.ConnectError("down", request=req)
        if path == "/health":
            return httpx.Response(200, json={"ok": True})
        if req.headers.get("authorization") != f"Token {w['token']}":
            return httpx.Response(401)
        if path == "/app-session" and method == "POST":
            body = json.loads(req.content)
            assert body == {"client": "rt-edge", "ttl_s": 30}
            self.n += 1
            sid = f"{host}-{self.n}"
            w["sessions"].add(sid)
            return httpx.Response(201, json={"session_id": sid, "ttl_s": 30})
        if path.startswith("/app-session/"):
            sid = path.split("/")[2]
            if sid not in w["sessions"]:
                return httpx.Response(404)
            if method == "DELETE":
                w["sessions"].discard(sid)
                return httpx.Response(204)
            return httpx.Response(200, json={"ttl_s": 30})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200, json={"choices": [{"message": {"content": f"from-{host}"}}]}
            )
        return httpx.Response(404)

    def of(self, method, path_prefix):
        return [
            c for c in self.calls if c[1] == method and c[2].startswith(path_prefix)
        ]


def ep(host, token="t", local=False, fallback=None):
    return Endpoint(
        "llm", host, f"http://{host}:8080", Credential(token, fallback), local
    )


@pytest.fixture
def workers():
    w = FakeWorkers()
    w.created = []
    yield w
    for ws in w.created:  # no heartbeat thread may outlive its test
        ws.stop()


def sessions(workers, *eps, clock=None):
    ws = WorkerSessions(
        list(eps),
        30,
        transport=httpx.MockTransport(workers),
        clock=clock or Clock(),
    )
    workers.created.append(ws)
    return ws


def test_session_lifecycle_open_heartbeat_reopen_close(workers):
    workers.add("local")
    ws = sessions(workers, ep("local", local=True))
    s = ws.start()
    assert ws.current() == ("http://local:8080", "t")
    assert workers.of("POST", "/app-session")[0][3] == "Token t"
    ws.tick()
    assert workers.calls[-1][2] == f"/app-session/{s.id}/heartbeat"
    workers.up["local"]["sessions"].clear()  # worker forgot it (expired) -> 404
    ws.tick()
    assert ws.snapshot()["session_id"] != s.id
    sid = ws.snapshot()["session_id"]
    ws.stop()
    assert workers.calls[-1][1:3] == ("DELETE", f"/app-session/{sid}")
    assert workers.up["local"]["sessions"] == set()


def test_start_fails_explicitly_when_no_worker(workers):
    ws = sessions(workers, ep("local"), ep("cyborg"))
    with pytest.raises(ServiceError) as e:
        ws.start()
    assert e.value.kind == "unreachable"


def test_failover_on_request_error_and_return_to_local(workers):
    clock = Clock()
    workers.add("local")
    workers.add("cyborg")
    ws = sessions(workers, ep("local", local=True), ep("cyborg"), clock=clock)
    ws.start()
    client = LlmClient(ws, LlmSettings(), transport=httpx.MockTransport(workers))
    del workers.up["local"]  # local worker dies
    assert client.translate("x", "pl") == "from-cyborg"
    snap = ws.snapshot()
    assert snap["node"] == "cyborg" and "http://local:8080" in snap["down"]
    workers.add("local")  # back, but still marked down
    ws.tick()
    assert ws.snapshot()["node"] == "cyborg"
    clock.t = 31.0  # down_s elapsed: next heartbeat round returns to local
    ws.tick()
    snap = ws.snapshot()
    assert snap["node"] == "local" and snap["local"] is True
    assert client.translate("x", "pl") == "from-local"
    # the cyborg session was closed when switching back
    assert workers.up["cyborg"]["sessions"] == set()
    ws.stop()


def test_heartbeat_failure_fails_over(workers):
    workers.add("local")
    workers.add("cyborg")
    ws = sessions(workers, ep("local"), ep("cyborg"))
    ws.start()
    del workers.up["local"]
    ws.tick()
    assert ws.snapshot()["node"] == "cyborg"
    ws.stop()


def test_local_worker_restart_uses_local_token_file(workers, tmp_path):
    lt = tmp_path / "local-token"
    lt.write_text("sha256:new\n")
    workers.add("local", token="sha256:old")
    ws = sessions(
        workers, ep("local", token="sha256:old", local=True, fallback=str(lt))
    )
    ws.start()
    client = LlmClient(ws, LlmSettings(), transport=httpx.MockTransport(workers))
    workers.up["local"]["token"] = "sha256:new"  # worker restarted
    assert client.translate("x", "pl") == "from-local"
    assert ws.snapshot()["offline"] is True
    workers.up["local"]["token"] = "sha256:other"  # file stale: no valid token
    with pytest.raises(ServiceError) as e:
        client.translate("x", "pl")
    # failed over; no other worker -> explicit error, no session
    assert e.value.kind == "unreachable" and ws.snapshot()["active"] is None


def test_switch_to_another_worker_notifies_on_switch(workers):
    # the edge warms the new worker up (live test 2026-10-31 §6)
    clock = Clock()
    workers.add("local")
    workers.add("cyborg")
    ws = sessions(workers, ep("local", local=True), ep("cyborg"), clock=clock)
    switched = []
    ws.on_switch = switched.append
    ws.start()
    assert switched == []  # start is warmed up by the edge itself
    del workers.up["local"]
    ws.tick()
    assert switched == ["http://cyborg:8080"]
    workers.up["cyborg"]["sessions"].clear()  # expired: reopened on the same worker
    ws.tick()
    assert switched == ["http://cyborg:8080"]
    workers.add("local")
    clock.t = 31.0
    ws.tick()
    assert switched == ["http://cyborg:8080", "http://local:8080"]
    ws.stop()


def test_llm_warm_up_is_one_tiny_completion(workers):
    workers.add("local")
    ws = sessions(workers, ep("local", local=True))
    ws.start()
    bodies = []

    def handle(req):
        bodies.append(json.loads(req.content))
        return workers(req)

    client = LlmClient(ws, LlmSettings(), transport=httpx.MockTransport(handle))
    client.warm_up()
    assert len(bodies) == 1
    assert bodies[0]["max_tokens"] == 1 and "stream" not in bodies[0]
    del workers.up["local"]
    with pytest.raises(ServiceError):  # explicit error, no failover
        client.warm_up()
    assert ws.snapshot()["node"] == "local"
    ws.stop()
