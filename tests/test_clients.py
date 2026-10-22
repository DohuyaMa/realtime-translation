import io
import json
import wave

import httpx
import numpy as np
import pytest

from src.clients import ClientFactories, client_factories
from src.clients.asr import AsrClient
from src.clients.base import ServiceClient, ServiceError
from src.clients.tts import TtsClient, decode_wav
from src.clients.llm import LlmClient, StaticTarget
from src.core.runtime import EdgeSettings, LlmSettings


def multipart_fields(req):
    from email import message_from_bytes

    msg = message_from_bytes(b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.content)
    out = {}
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename() is None:
            out[name] = part.get_payload(decode=True).decode()
    return out


def wav_bytes(frames, rate=24000, width=2, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


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
    hits = []

    def always_401(req):
        hits.append(1)
        return httpx.Response(401)

    with pytest.raises(ServiceError) as e:
        llm(always_401, t2).translate("a", "ru")
    assert e.value.kind == "auth" and t2.calls == ["auth"] and len(hits) == 2


def test_llm_503_is_busy_and_unreachable_retries_on_new_target():
    def handler(req):
        if req.url.host == "llm1":
            raise httpx.ConnectError("x", request=req)
        return ok("Hi")

    t = FakeTarget(["http://llm1", "http://llm2"])
    assert llm(handler, t).translate("a", "uk") == "Hi"
    assert t.calls == ["unreachable"]
    t2 = FakeTarget()
    hits = []

    def always_503(req):
        hits.append(1)
        return httpx.Response(503)

    with pytest.raises(ServiceError) as e:
        llm(always_503, t2).translate("a", "uk")
    assert e.value.kind == "busy" and t2.calls == ["busy"] and len(hits) == 2


@pytest.mark.parametrize(
    "first,kind",
    [
        (lambda req: httpx.Response(503), "busy"),
        (lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=req)), "timeout"),
    ],
)
def test_llm_transient_then_200_retries_once(first, kind):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return first(req) if n["c"] == 1 else ok("Hi")

    t = FakeTarget()
    assert llm(handler, t).translate("a", "uk") == "Hi"
    assert t.calls == [kind] and n["c"] == 2


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
        fields = multipart_fields(req)
        assert fields["priority"] == "batch"
        assert fields["chunk_seconds"] == "30.0"
        assert fields["response_format"] == "verbose_json"
        return httpx.Response(200, json={"text": " hi ", "language": "en"})

    c = AsrClient("http://asr", transport=httpx.MockTransport(handler))
    r = c.transcribe(np.ones(100, dtype=np.int16), priority="batch", chunk_seconds=30.0, timeout_s=5)
    assert r.text == "hi"


def test_asr_default_priority_realtime():
    def handler(req):
        assert multipart_fields(req)["priority"] == "realtime"
        assert "chunk_seconds" not in multipart_fields(req)
        return httpx.Response(200, json={"text": "x"})

    AsrClient("http://asr", transport=httpx.MockTransport(handler)).transcribe(np.ones(10, dtype=np.int16))


def test_client_factories():
    f = client_factories(EdgeSettings())
    assert isinstance(f, ClientFactories)
    assert f.asr("http://a:1/").base_url == "http://a:1"
    assert f.tts("http://t:1").service == "tts"


def tts(handler):
    return TtsClient("http://tts", transport=httpx.MockTransport(handler))


def test_tts_roundtrip_and_request_body():
    pcm = np.array([0, 16384, -16384], dtype="<i2").tobytes()

    def handler(req):
        assert req.url.path == "/v1/audio/speech"
        assert json.loads(req.content) == {"input": "hi", "response_format": "wav", "voice": "v", "speed": 1.25}
        return httpx.Response(200, content=wav_bytes(pcm))

    audio, rate = tts(handler).synthesize("hi", voice="v", speed=1.25)
    assert rate == 24000 and audio.dtype == np.float32
    np.testing.assert_allclose(audio, [0.0, 0.5, -0.5])


def test_tts_x_sample_rate_header_overrides_wav_rate():
    resp = httpx.Response(200, content=wav_bytes(b"\x00\x00" * 4), headers={"X-Sample-Rate": "22050"})
    assert tts(lambda r: resp).synthesize("x")[1] == 22050


def test_decode_wav_stereo_downmix_and_non_pcm16():
    frames = np.array([16384, -16384, 16384, 16384], dtype="<i2").tobytes()
    audio, rate = decode_wav(wav_bytes(frames, rate=16000, channels=2))
    assert rate == 16000
    np.testing.assert_allclose(audio, [0.0, 0.5])
    with pytest.raises(ValueError):
        decode_wav(wav_bytes(b"\x00" * 8, width=4))


def test_tts_invalid_wav_is_service_error():
    with pytest.raises(ServiceError) as e:
        tts(lambda r: httpx.Response(200, content=b"not a wav")).synthesize("x")
    assert (e.value.service, e.value.kind) == ("tts", "http")
    with pytest.raises(ServiceError) as e:
        tts(lambda r: httpx.Response(200, content=wav_bytes(b"\x00" * 8, width=4))).synthesize("x")
    assert e.value.kind == "http"


def test_service_client_health_status_and_error_mapping():
    ok_c = ServiceClient(
        "http://s/prefix/", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ready": True}))
    )
    assert ok_c.health() is True and ok_c.status() == {"ready": True}
    bad = ServiceClient("http://s", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert bad.health() is False and bad.status() is None

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    for handler, kind in ((refuse, "unreachable"), (slow, "timeout")):
        c = ServiceClient("http://s", transport=httpx.MockTransport(handler))
        with pytest.raises(ServiceError) as e:
            c._request("GET", "/x")
        assert e.value.kind == kind
        assert c.health() is False
    auth = ServiceClient("http://s", transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ServiceError) as e:
        auth._request("GET", "/x")
    assert e.value.kind == "auth" and e.value.status_code == 403
