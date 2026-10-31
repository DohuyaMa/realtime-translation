import pytest

from src.clients import (
    Credential,
    Directory,
    Endpoint,
    EndpointPool,
    ServiceEntry,
    ServiceError,
    SpeechPools,
    build_pools,
)
from src.core.runtime import EdgeSettings, LocalUrls


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def ep(url, node=None, local=False):
    return Endpoint("asr", node or url, url, Credential(), local=local)


def make(urls=("a", "b"), clock=None):
    kw = {"clock": clock} if clock else {}
    eps = [ep(u, local=(i == 0)) for i, u in enumerate(urls)]
    return EndpointPool("asr", eps, lambda e: e.url, **kw)


def test_pick_uses_priority_order_not_load():
    p = make()
    p.update_status("a", ready=True, pending=3)
    p.update_status("b", ready=True, pending=0)
    assert p.pick() == "a"


def test_unknown_health_counts_as_candidate():
    assert make().pick() == "a"


def test_unhealthy_local_is_skipped_and_returns_when_healthy(caplog):
    p = make()
    p.update_status("a", ready=False, pending=None)
    assert p.call(lambda u: u) == "b"
    assert p.active() == {"url": "b", "node": "b", "local": False, "offline": False}
    p.update_status("a", ready=True, pending=0)  # local is back
    assert p.call(lambda u: u) == "a"
    assert p.active()["local"] is True


def test_failover_retries_once_on_next_endpoint():
    p = make()
    calls = []

    def fn(url):
        calls.append(url)
        if url == "a":
            raise ServiceError("asr", "unreachable")
        return f"ok-{url}"

    assert p.call(fn) == "ok-b"
    assert calls == ["a", "b"]
    assert p.active_url == "b"


def test_non_retryable_error_propagates_without_retry():
    p = make()
    calls = []

    def fn(url):
        calls.append(url)
        raise ServiceError("asr", "http", status_code=400)

    with pytest.raises(ServiceError):
        p.call(fn)
    assert len(calls) == 1


def test_http_503_is_retryable_other_http_is_not():
    p = make()

    def fn(url):
        if url == "a":
            raise ServiceError("asr", "http", status_code=503)
        return url

    assert p.call(fn) == "b"
    q = make()
    with pytest.raises(ServiceError):
        q.call(
            lambda u: (_ for _ in ()).throw(
                ServiceError("asr", "http", status_code=500)
            )
        )


def test_down_endpoint_returns_to_local_after_down_s():
    clock = Clock()
    p = make(clock=clock)
    p.call(
        lambda u: (
            (_ for _ in ()).throw(ServiceError("asr", "timeout")) if u == "a" else u
        )
    )
    assert p.pick() == "b"
    clock.t = 31.0
    assert p.call(lambda u: u) == "a"


def test_no_endpoints_available_raises_unreachable():
    p = make(urls=("a",))
    with pytest.raises(ServiceError) as ei:
        p.call(lambda u: (_ for _ in ()).throw(ServiceError("asr", "unreachable")))
    assert ei.value.kind == "unreachable"


def test_build_pools_local_first_then_allowed_cluster_services():
    d = Directory(
        [
            ServiceEntry("asr", "me", "http://me:8091", "t-me"),
            ServiceEntry("asr", "gpu", "http://gpu:8091", "t-gpu"),
            ServiceEntry("asr", "other", "http://other:8091", "t-o"),
            ServiceEntry("tts", "gpu", "http://gpu:8093", "t-tts"),
        ],
        "django:http://d",
    )
    s = EdgeSettings(
        local=LocalUrls(asr="http://127.0.0.1:8091", tts=None),
        allowed_services=("asr:me", "asr:gpu", "tts:gpu"),
    )
    from src.clients import discovery

    orig = discovery.socket.gethostname
    discovery.socket.gethostname = lambda: "me"
    try:
        pools = build_pools(s, d)
    finally:
        discovery.socket.gethostname = orig
    assert isinstance(pools, SpeechPools)
    assert pools.asr.urls == ["http://127.0.0.1:8091", "http://gpu:8091"]
    assert pools.asr.endpoints["http://127.0.0.1:8091"].credential.value == "t-me"
    assert pools.tts.urls == ["http://gpu:8093"]
    assert set(pools.by_name()) == {"asr", "tts"}
    pools.close()
    bare = build_pools(EdgeSettings(), Directory())
    assert bare.tts is None and bare.asr.urls == ["http://127.0.0.1:8091"]
    bare.close()


def test_auth_error_fails_over_to_next_node():
    p = make()

    def fn(url):
        if url == "a":
            raise ServiceError("asr", "auth", status_code=401)
        return url

    assert p.call(fn) == "b"
