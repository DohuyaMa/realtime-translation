from src.clients import EndpointPool, ServiceHealthMonitor, SpeechPools


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


class FakeLease:
    def snapshot(self):
        return {"base_url": "http://w", "service": "s", "model": "m", "error": None}


def pools(statuses):
    asr = EndpointPool("asr", list(statuses), lambda u: FakeClient(statuses[u]))
    return SpeechPools(asr=asr, tts=None)


def test_monitor_llm_entry_from_lease_and_worker_status():
    llm = FakeLlm({"model": {"loaded": True, "path": "/m/q.gguf"}})
    m = ServiceHealthMonitor(pools({"a": None}), llm, FakeLease())
    e = m.poll_once()["llm"][0]
    assert e["url"] == "http://w"
    assert e["ready"] is True
    assert e["model"] == "q.gguf"
    assert e["pending"] is None
    assert e["lease"]["service"] == "s"


def test_monitor_updates_pool():
    st = {
        "a": {"ready": True, "pending": 4, "model": {"id": "x"}, "device": "cuda"},
        "b": {"ready": True, "pending": 1, "model": {"id": "x"}, "device": "cpu"},
    }
    p = pools(st)
    m = ServiceHealthMonitor(p, None, None)
    snap = m.poll_once()
    assert snap["asr"][0] == {
        "service": "asr",
        "url": "a",
        "connected": True,
        "ready": True,
        "pending": 4,
        "model": "x",
        "device": "cuda",
        "error": None,
    }
    assert p.asr.pick() == "b"


def test_unreachable_endpoint_and_initial_snapshot():
    m = ServiceHealthMonitor(pools({"a": None}), None, None)
    assert m.snapshot()["asr"][0]["error"] == "unreachable"
    assert m.poll_once()["asr"][0]["connected"] is False
