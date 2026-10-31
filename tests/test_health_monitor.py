from src.clients import (
    Credential,
    Endpoint,
    EndpointPool,
    ServiceHealthMonitor,
    SpeechPools,
)


class FakeClient:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


class FakeLlm:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


class FakeSessions:
    def snapshot(self):
        return {"active": "http://w", "node": "cyborg", "session_id": "s1"}


def pools(statuses):
    eps = [Endpoint("asr", u, u, Credential()) for u in statuses]
    asr = EndpointPool("asr", eps, lambda e: FakeClient(statuses[e.url]))
    return SpeechPools(asr=asr, tts=None)


def test_monitor_llm_entry_from_session_and_worker_status():
    llm = FakeLlm({"model": {"loaded": True, "path": "/m/q.gguf"}})
    m = ServiceHealthMonitor(pools({"a": None}), llm, FakeSessions())
    e = m.poll_once()["llm"][0]
    assert e["url"] == "http://w"
    assert e["ready"] is True
    assert e["model"] == "q.gguf"
    assert e["session"]["node"] == "cyborg"


def test_monitor_updates_pool_health_and_skips_unhealthy_first():
    st = {
        "a": {
            "ready": False,
            "pending": 0,
            "model": {"id": "x", "state": "loading"},
            "device": "cuda",
        },
        "b": {
            "ready": True,
            "pending": 1,
            "model": {"id": "x", "state": "ready"},
            "device": "cpu",
            "memory": {"rss_mb": 900, "swap_mb": 0, "vram_mb": None},
        },
    }
    p = pools(st)
    m = ServiceHealthMonitor(p, None, None)
    snap = m.poll_once()
    assert snap["asr"][1] == {
        "service": "asr",
        "url": "b",
        "connected": True,
        "ready": True,
        "pending": 1,
        "model": "x",
        "state": "ready",
        "model_error": None,
        "download": None,
        "memory": {"rss_mb": 900.0, "swap_mb": 0.0, "vram_mb": None},
        "dropped": None,
        "device": "cpu",
        "error": None,
    }
    assert p.asr.pick() == "b"


def test_unreachable_endpoint_and_initial_snapshot():
    m = ServiceHealthMonitor(pools({"a": None}), None, None)
    assert m.snapshot()["asr"][0]["error"] == "unreachable"
    assert m.poll_once()["asr"][0]["connected"] is False
