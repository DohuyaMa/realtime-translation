import json

import httpx
import numpy as np
import pytest

from src.clients import ClientFactories, client_factories
from src.clients.asr import AsrClient
from src.clients.base import ServiceError
from src.clients.llm import LlmClient, StaticTarget
from src.core.runtime import EdgeSettings, LlmSettings


class FakeTarget:
    def __init__(self, urls=("http://llm1",)):
        self.urls = list(urls)
        self.calls = []

    def current(self):
        return self.urls[0], "sha256:abc"

    def invalidate(self, kind):
        self.calls.append(kind)
        if len(self.urls) > 1:
            self.urls.pop(0)


def ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def llm(handler, target=None):
    return LlmClient(target or FakeTarget(), LlmSettings(), transport=httpx.MockTransport(handler))


def test_llm_request_body():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return ok("Hello")

    out = llm(handler).translate("привіт світе", "uk", [("a", "A"), ("b", "B")])
    assert out == "Hello"
    b = seen["body"]
    assert seen["auth"] == "Bearer sha256:abc"
    assert seen["url"] == "http://llm1/v1/chat/completions"
    assert "Ukrainian" in b["messages"][0]["content"]
    assert [m["role"] for m in b["messages"]] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert b["messages"][-1]["content"] == "привіт світе"
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert b["max_tokens"] == 32 + 3 * 2 and b["model"] == "local" and b["temperature"] == 0.1


def test_llm_strips_think_block():
    assert llm(lambda r: ok("<think>x</think> Hello")).translate("a", "pl") == "Hello"


def test_llm_empty_content_raises_service_error():
    with pytest.raises(ServiceError) as e:
        llm(lambda r: ok("<think>x</think>  ")).translate("a", "pl")
    assert (e.value.service, e.value.kind) == ("llm", "http")


def test_llm_401_invalidates_auth_and_retries_once():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(401) if n["c"] == 1 else ok("Hi")

    t = FakeTarget()
    assert llm(handler, t).translate("a", "ru") == "Hi"
    assert t.calls == ["auth"]
    t2 = FakeTarget()
    with pytest.raises(ServiceError) as e:
        llm(lambda r: httpx.Response(401), t2).translate("a", "ru")
    assert e.value.kind == "auth" and t2.calls == ["auth"]


def test_llm_503_is_busy_and_unreachable_retries_on_new_target():
    def handler(req):
        if req.url.host == "llm1":
            raise httpx.ConnectError("x", request=req)
        return ok("Hi")

    t = FakeTarget(["http://llm1", "http://llm2"])
    assert llm(handler, t).translate("a", "uk") == "Hi"
    assert t.calls == ["unreachable"]
    t2 = FakeTarget()
    with pytest.raises(ServiceError):
        llm(lambda r: httpx.Response(503), t2).translate("a", "uk")
    assert t2.calls == ["busy"]


def test_llm_english_passthrough_no_http():
    def handler(req):
        raise AssertionError("no http")

    assert llm(handler).translate("hello", "en") == "hello"
    with pytest.raises(ValueError):
        llm(handler).translate("x", "de")


def test_llm_status_uses_bearer():
    def handler(req):
        assert req.url.path == "/status" and req.headers["authorization"] == "Bearer sha256:abc"
        return httpx.Response(200, json={"model": {"loaded": True}})

    assert llm(handler).status() == {"model": {"loaded": True}}


def test_static_target_reads_token_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text("secret\n")
    t = StaticTarget("http://l:1/", str(f))
    assert t.current() == ("http://l:1", "secret")
    t.invalidate("auth")


def test_asr_client_sends_priority_and_chunk_seconds():
    def handler(req):
        body = req.content
        assert b'name="priority"' in body and b"batch" in body
        assert b'name="chunk_seconds"' in body and b"30.0" in body
        return httpx.Response(200, json={"text": " hi ", "language": "en"})

    c = AsrClient("http://asr", transport=httpx.MockTransport(handler))
    r = c.transcribe(np.ones(100, dtype=np.int16), priority="batch", chunk_seconds=30.0, timeout_s=5)
    assert r.text == "hi"


def test_asr_default_priority_realtime():
    def handler(req):
        assert b"realtime" in req.content
        return httpx.Response(200, json={"text": "x"})

    AsrClient("http://asr", transport=httpx.MockTransport(handler)).transcribe(np.ones(10, dtype=np.int16))


def test_client_factories():
    f = client_factories(EdgeSettings())
    assert isinstance(f, ClientFactories)
    assert f.asr("http://a:1/").base_url == "http://a:1"
    assert f.tts("http://t:1").service == "tts"
