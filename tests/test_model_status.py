"""Actual-model status of rt-asr/rt-tts, explicit load/unload, 503 not_ready."""

import httpx
import pytest

from src.clients.base import ServiceClient, ServiceError
from src.clients.health import _from_status
from src.clients.model_status import parse_status
from src.clients.pool import is_retryable

DOWNLOADING = {
    "ready": False,
    "device": "cuda",
    "model": {
        "id": "whisper-large-v3",
        "state": "downloading",
        "error": None,
        "download": {"done_mb": 750, "total_mb": 3000},
        "device": "cuda",
    },
    "memory": {"rss_mb": 2800, "swap_mb": 5700, "vram_mb": None},
    "dropped_segments": {"hallucination": 3, "repetition": 0},
}


def test_parse_status_actual_model_download_and_memory():
    ms = parse_status(DOWNLOADING)
    assert (ms.model_id, ms.state, ms.transitional) == (
        "whisper-large-v3",
        "downloading",
        True,
    )
    assert ms.download_percent == 25
    assert (ms.rss_mb, ms.swap_mb, ms.vram_mb) == (2800, 5700, None)
    assert ms.ready is False
    assert ms.dropped == {"hallucination": 3}


def test_parse_status_error_state():
    ms = parse_status(
        {"model": {"id": "w", "state": "error", "error": "CUDA out of memory"}}
    )
    assert ms.state == "error" and ms.error == "CUDA out of memory"
    assert not ms.transitional and not ms.ready


def test_health_entry_carries_state_and_memory():
    e = _from_status("asr", "http://a", DOWNLOADING)
    assert e["model"] == "whisper-large-v3" and e["state"] == "downloading"
    assert e["download"] == {"done_mb": 750.0, "total_mb": 3000.0}
    assert e["memory"] == {"rss_mb": 2800.0, "swap_mb": 5700.0, "vram_mb": None}
    assert e["ready"] is False and e["connected"] is True


def _client(handler):
    return ServiceClient("http://svc", transport=httpx.MockTransport(handler))


def test_load_and_unload_model_post():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        return httpx.Response(202, json={"state": "loading"})

    c = _client(handler)
    assert c.load_model() == {"state": "loading"}
    c.unload_model()
    assert seen == [("POST", "/model/load"), ("POST", "/model/unload")]


def test_get_status_raises_status_returns_none():
    c = _client(lambda req: httpx.Response(401))
    with pytest.raises(ServiceError) as e:
        c.get_status()
    assert e.value.kind == "auth"
    assert c.status() is None


def test_503_model_not_ready_is_explicit_and_fails_over():
    body = {
        "error": {
            "message": "Model is still loading",
            "type": "server_error",
            "code": "model_not_ready",
        }
    }
    c = _client(lambda req: httpx.Response(503, json=body))
    with pytest.raises(ServiceError) as e:
        c.get_config()
    assert e.value.kind == "not_ready" and "still loading" in str(e.value)
    assert is_retryable(e.value)


def test_503_busy_stays_http():
    body = {"error": {"message": "busy", "type": "server_error", "code": "server_busy"}}
    c = _client(lambda req: httpx.Response(503, json=body))
    with pytest.raises(ServiceError) as e:
        c.get_config()
    assert e.value.kind == "http" and e.value.status_code == 503
