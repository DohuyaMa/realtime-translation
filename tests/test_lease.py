import json
import threading

import httpx
import pytest

from src.clients import LeaseKeeper, ServiceError
from src.clients.lease import DjangoLeaseApi
from src.core.runtime import LlmSettings

EXP = "2030-01-01T00:00:00Z"


def lease_json(lid, name, token="sha256:t1"):
    return {
        "id": lid,
        "service": {
            "name": name,
            "base_url": f"http://{name}:8080",
            "current_model": "m.gguf",
        },
        "worker_token": token,
        "ttl_s": 60,
        "heartbeat_interval_s": 20,
        "expires_at": EXP,
    }


class FakeDjango:
    def __init__(self):
        self.calls = []  # (host, method, path, body, headers)
        self.handlers = {}  # host -> fn(request, body) -> Response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        host = request.url.host
        self.calls.append(
            (host, request.method, request.url.path, body, dict(request.headers))
        )
        h = self.handlers.get(host)
        if h is None:
            raise httpx.ConnectError("down")
        return h(request, body)

    def of(self, host, method):
        return [c for c in self.calls if c[0] == host and c[1] == method]


@pytest.fixture
def env(tmp_path):
    tf = tmp_path / "tok"
    tf.write_text("usertoken\n")
    fake = FakeDjango()
    transport = httpx.MockTransport(fake)

    def make(urls=("http://a", "http://b", "http://c"), service=None):
        s = LlmSettings(django_urls=urls, django_token_file=str(tf), service=service)
        gate = threading.Event()  # heartbeat thread never ticks on its own
        keeper = LeaseKeeper(
            s,
            "rt-edge@test",
            api_factory=lambda u: DjangoLeaseApi(u, str(tf), transport=transport),
            sleep=lambda _s: gate.wait(5),
        )
        keeper._gate = gate
        return keeper

    return fake, make


def ok_acquire(lid, name, token="sha256:t1"):
    return lambda r, b: httpx.Response(201, json=lease_json(lid, name, token))


def finish(k):
    k._gate.set()
    k.stop()


def test_acquire_on_first_reachable_django_then_mirrors(env):
    fake, make = env
    fake.handlers["b"] = ok_acquire("b1", "w1")
    fake.handlers["c"] = ok_acquire("c1", "w1")
    k = make()
    k.start()
    assert k.current() == ("http://w1:8080", "sha256:t1")
    assert fake.of("c", "POST")[0][3]["service"] == "w1"
    assert fake.of("b", "POST")[0][3]["service"] is None
    assert len(k.snapshot()["mirrors"]) == 1
    finish(k)


def test_mirror_failure_is_not_fatal(env):
    fake, make = env
    fake.handlers["a"] = ok_acquire("a1", "w1")
    fake.handlers["c"] = lambda r, b: httpx.Response(409, json={"detail": "leased"})
    k = make()
    assert k.start().service_name == "w1"
    assert k.snapshot()["mirrors"] == []
    finish(k)


def test_no_django_available_raises(env):
    _, make = env
    k = make()
    with pytest.raises(ServiceError) as ei:
        k.start()
    assert ei.value.kind == "unreachable"
    with pytest.raises(ServiceError):
        k.current()


def test_current_before_start_raises(env):
    _, make = env
    with pytest.raises(ServiceError) as ei:
        make().current()
    assert ei.value.kind == "unreachable"


def test_heartbeat_refreshes_worker_token(env):
    fake, make = env
    fake.handlers["a"] = lambda r, b: (
        httpx.Response(201, json=lease_json("a1", "w1"))
        if r.method == "POST" and r.url.path == "/api/llm/leases/"
        else httpx.Response(200, json=lease_json("a1", "w1", "sha256:t2"))
    )
    k = make(("http://a",))
    k.start()
    k.tick()
    assert k.current()[1] == "sha256:t2"
    assert fake.calls[-1][2] == "/api/llm/leases/a1/heartbeat/"
    assert fake.calls[-1][3] == {}
    finish(k)


def test_heartbeat_410_triggers_reacquire(env):
    fake, make = env
    leases = iter([lease_json("a1", "w1"), lease_json("a2", "w2")])

    def h(r, b):
        if r.url.path.endswith("heartbeat/"):
            return httpx.Response(410, json={"detail": "lease expired"})
        if r.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201, json=next(leases))

    fake.handlers["a"] = h
    k = make(("http://a",))
    k.start()
    k.tick()
    assert k.current()[0] == "http://w2:8080"
    finish(k)


def test_invalidate_auth_heartbeats_synchronously(env):
    fake, make = env

    def h(r, b):
        if r.url.path.endswith("heartbeat/"):
            return httpx.Response(200, json=lease_json("a1", "w1", "sha256:new"))
        return httpx.Response(201, json=lease_json("a1", "w1"))

    fake.handlers["a"] = h
    k = make(("http://a",))
    k.start()
    k.invalidate("auth")
    assert k.current()[1] == "sha256:new"
    finish(k)


@pytest.mark.parametrize("kind", ["unreachable", "timeout", "busy"])
def test_invalidate_unreachable_releases_and_reacquires(env, kind):
    fake, make = env
    leases = iter([lease_json("a1", "w1"), lease_json("a2", "w2")])

    def h(r, b):
        if r.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201, json=next(leases))

    fake.handlers["a"] = h
    k = make(("http://a",), service="w1")
    k.start()
    k.invalidate(kind)
    assert fake.of("a", "DELETE")[0][2] == "/api/llm/leases/a1/"
    methods = [c[1] for c in fake.calls]
    assert methods.index("DELETE") < len(methods) - 1 - methods[::-1].index("POST")
    assert fake.of("a", "POST")[1][3]["service"] is None
    assert k.current()[0] == "http://w2:8080"
    finish(k)


def test_stop_releases_all_and_is_idempotent(env):
    fake, make = env
    fake.handlers["a"] = ok_acquire("a1", "w1")
    fake.handlers["b"] = ok_acquire("b1", "w1")
    for h in ("a", "b"):
        base = fake.handlers[h]
        fake.handlers[h] = lambda r, b, base=base: (
            httpx.Response(204) if r.method == "DELETE" else base(r, b)
        )
    k = make(("http://a", "http://b"))
    k.start()
    finish(k)
    k.stop()
    assert len(fake.of("a", "DELETE")) == 1
    assert len(fake.of("b", "DELETE")) == 1
    with pytest.raises(ServiceError):
        k.current()


def test_request_bodies_match_spec(env):
    fake, make = env
    fake.handlers["a"] = ok_acquire("a1", "w1")
    k = make(("http://a",))
    k.start()
    host, method, path, body, headers = fake.calls[0]
    assert (method, path) == ("POST", "/api/llm/leases/")
    assert body == {"client_name": "rt-edge@test", "service": None, "ttl_s": 60}
    assert headers["authorization"] == "Token usertoken"
    finish(k)


def test_heartbeat_server_error_after_deadline_reacquires(env):
    fake, make = env
    now = [0.0]
    leases = iter([lease_json("a1", "w1"), lease_json("a2", "w2")])

    def h(r, b):
        if r.url.path.endswith("heartbeat/"):
            return httpx.Response(500, json={"detail": "boom"})
        if r.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201, json=next(leases))

    fake.handlers["a"] = h
    k = make(("http://a",))
    k._clock = lambda: now[0]
    k.start()
    k.tick()  # before deadline: kept, error recorded
    assert k.current()[0] == "http://w1:8080"
    assert "500" in k.snapshot()["error"]
    now[0] = 61.0
    k.tick()
    assert k.current()[0] == "http://w2:8080"
    assert k.snapshot()["error"] is None
    finish(k)


# -- no-lease mode (owner 2026-10-31: translation survives Django outages) --


def make_with_direct(tmp_path, transport, urls=("http://a", "http://b")):
    tf = tmp_path / "tok"
    tf.write_text("usertoken\n")
    wt = tmp_path / "wt"
    wt.write_text("sha256:local\n")
    s = LlmSettings(
        django_urls=urls,
        django_token_file=str(tf),
        direct_url="http://127.0.0.1:8080/",
        direct_token_file=str(wt),
    )
    gate = threading.Event()
    k = LeaseKeeper(
        s,
        "rt-edge@test",
        api_factory=lambda u: DjangoLeaseApi(u, str(tf), transport=transport),
        sleep=lambda _s: gate.wait(5),
    )
    k._gate = gate
    return k


def test_all_django_down_enters_no_lease_mode_with_direct_url(tmp_path, caplog):
    fake = FakeDjango()
    k = make_with_direct(tmp_path, httpx.MockTransport(fake))
    assert k.start() is None
    assert k.current() == ("http://127.0.0.1:8080", "sha256:local")
    snap = k.snapshot()
    assert snap["mode"] == "no_lease" and snap["no_lease_source"] == "direct_url"
    assert snap["base_url"] == "http://127.0.0.1:8080"
    assert snap["skipped"] == ["http://a", "http://b"]
    finish(k)


def test_all_django_down_without_target_raises(env):
    _, make = env
    with pytest.raises(ServiceError) as ei:
        make().start()
    assert "direct_url" in str(ei.value)


def test_django_refusal_does_not_enter_no_lease(tmp_path):
    fake = FakeDjango()
    fake.handlers["b"] = lambda r, b: httpx.Response(503, json={"detail": "none"})
    k = make_with_direct(tmp_path, httpx.MockTransport(fake))
    with pytest.raises(ServiceError):
        k.start()
    assert k.snapshot()["mode"] is None


def test_partial_down_leases_on_reachable_and_mirrors_on_recovery(env):
    fake, make = env
    fake.handlers["b"] = lambda r, b: (
        httpx.Response(200, json=lease_json("b1", "w1"))
        if r.url.path.endswith("heartbeat/")
        else httpx.Response(201, json=lease_json("b1", "w1"))
    )
    k = make(("http://a", "http://b"))
    assert k.start().django_url == "http://b"
    snap = k.snapshot()
    assert snap["mode"] == "lease" and snap["skipped"] == ["http://a"]
    fake.handlers["a"] = ok_acquire("a9", "w1")  # a comes back
    k.tick()
    assert fake.of("a", "POST")[-1][3]["service"] == "w1"
    snap = k.snapshot()
    assert snap["skipped"] == [] and snap["mirrors"][0]["django"] == "http://a"
    finish(k)


def test_recovery_leaves_no_lease_mode(tmp_path):
    fake = FakeDjango()
    k = make_with_direct(tmp_path, httpx.MockTransport(fake))
    assert k.start() is None
    k.tick()  # still down: stays in no-lease
    assert k.snapshot()["mode"] == "no_lease"
    fake.handlers["a"] = ok_acquire("a1", "w1")
    k.tick()
    assert k.current() == ("http://w1:8080", "sha256:t1")
    snap = k.snapshot()
    assert snap["mode"] == "lease" and snap["no_lease_source"] is None
    finish(k)


def test_django_lost_mid_session_uses_last_leased_worker(tmp_path):
    fake = FakeDjango()
    fake.handlers["a"] = ok_acquire("a1", "w1")
    k = make_with_direct(tmp_path, httpx.MockTransport(fake), urls=("http://a",))
    k.start()
    k._clock = lambda: 1e12  # the next heartbeat failure is past the deadline
    del fake.handlers["a"]  # django goes down
    k.tick()
    assert k.current() == ("http://w1:8080", "sha256:t1")
    assert k.snapshot()["no_lease_source"] == "last_lease:w1"
    # the cached worker fails too -> direct_url
    k.invalidate("unreachable")
    assert k.current() == ("http://127.0.0.1:8080", "sha256:local")
    assert k.snapshot()["no_lease_source"] == "direct_url"
    finish(k)
