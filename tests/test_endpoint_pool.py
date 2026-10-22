import pytest

from src.clients import EndpointPool, ServiceError, SpeechPools, build_pools
from src.core.runtime import EdgeSettings


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(urls=("a", "b"), clock=None):
    kw = {"clock": clock} if clock else {}
    return EndpointPool("asr", urls, lambda u: u, **kw)


def test_pick_prefers_ready_min_pending():
    p = make()
    p.update_status("a", ready=True, pending=3)
    p.update_status("b", ready=True, pending=0)
    assert p.pick() == "b"


def test_pick_tie_uses_list_order():
    p = make()
    p.update_status("a", ready=True, pending=1)
    p.update_status("b", ready=True, pending=1)
    assert p.pick() == "a"


def test_pick_falls_back_to_first_not_down_when_none_ready():
    p = make()
    assert p.pick() == "a"


def test_failover_repins_and_retries_once():
    p = make()
    calls = []

    def fn(url):
        calls.append(url)
        if url == "a":
            raise ServiceError("asr", "unreachable")
        return f"ok-{url}"

    assert p.call(fn) == "ok-b"
    assert calls == ["a", "b"]
    assert p.pinned_url == "b"
    assert p.pick() == "b"


def test_non_retryable_error_propagates_without_retry():
    p = make()
    calls = []

    def fn(url):
        calls.append(url)
        raise ServiceError("asr", "auth", status_code=401)

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


def test_down_endpoint_returns_after_down_s():
    clock = Clock()
    p = make(clock=clock)
    p.call(
        lambda u: (
            (_ for _ in ()).throw(ServiceError("asr", "timeout")) if u == "a" else u
        )
    )
    assert p.pick() == "b"
    clock.t = 31.0
    assert p.pick() == "a"


def test_no_endpoints_available_raises_unreachable():
    p = make(urls=("a",))
    with pytest.raises(ServiceError) as ei:
        p.call(lambda u: (_ for _ in ()).throw(ServiceError("asr", "unreachable")))
    assert ei.value.kind == "unreachable"


def test_build_pools_and_by_name():
    pools = build_pools(EdgeSettings(asr_urls=("http://a",), tts_urls=()))
    assert isinstance(pools, SpeechPools)
    assert pools.tts is None
    assert set(pools.by_name()) == {"asr"}
    pools2 = build_pools(EdgeSettings(asr_urls=("http://a",), tts_urls=("http://t",)))
    assert set(pools2.by_name()) == {"asr", "tts"}
    pools.close()
    pools2.close()
