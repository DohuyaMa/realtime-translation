import json
import stat

import httpx
import pytest

from src.clients import Directory, ServiceEntry, discover, endpoints
from src.core.runtime import EdgeSettings, LocalUrls

PAYLOAD = {
    "instance": "sktop",
    "generated_at": "2026-10-31T12:00:00Z",
    "services": [
        {
            "kind": "llm",
            "node": "sktop",
            "url": "http://sktop:8080",
            "token": "t-l1",
            "tier": 1,
            "models": ["m"],
            "healthy": True,
        },
        {
            "kind": "llm",
            "node": "cyborg",
            "url": "http://cyborg:8080/",
            "token": "t-l2",
            "tier": 2,
            "models": ["m"],
            "healthy": True,
        },
        {
            "kind": "tts",
            "node": "cyborg",
            "url": "http://cyborg:8093",
            "token": "t-t",
            "tier": None,
            "models": [],
            "healthy": False,
        },
        {"kind": "other", "node": "x", "url": "http://x", "token": ""},
    ],
}


@pytest.fixture
def env(tmp_path):
    tok = tmp_path / "drf"
    tok.write_text("drf-token\n")
    cache = tmp_path / "cache" / "app-services.json"
    seen = []

    def run(handlers, urls=("http://d1", "http://d2"), **kw):
        def handle(req):
            seen.append((req.url.host, req.url.path, req.headers.get("authorization")))
            h = handlers.get(req.url.host)
            if h is None:
                raise httpx.ConnectError("down", request=req)
            return h(req)

        s = EdgeSettings(django_urls=urls, django_token_file=str(tok), **kw)
        return s, discover(s, cache_path=cache, transport=httpx.MockTransport(handle))

    return run, cache, seen


def test_discovery_first_reachable_django_and_cache_0600(env):
    run, cache, seen = env
    s, d = run({"d2": lambda r: httpx.Response(200, json=PAYLOAD)})
    assert d.source == "django:http://d2" and d.instance == "sktop"
    assert [e.key for e in d.services] == ["llm:sktop", "llm:cyborg", "tts:cyborg"]
    assert d.services[1].url == "http://cyborg:8080"
    assert seen[-1] == ("d2", "/api/llm/app-services/", "Token drf-token")
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert json.loads(cache.read_text()) == PAYLOAD


def test_no_django_uses_cache_then_local_only(env):
    run, cache, _ = env
    run({"d1": lambda r: httpx.Response(200, json=PAYLOAD)})
    _, d = run({})
    assert d.source == "cache" and len(d.services) == 3 and len(d.errors) == 2
    cache.unlink()
    _, d = run({})
    assert d.source == "none" and d.services == []


def test_bad_payload_falls_through_to_next_django(env):
    run, _, _ = env
    _, d = run(
        {
            "d1": lambda r: httpx.Response(200, json={"oops": 1}),
            "d2": lambda r: httpx.Response(200, json=PAYLOAD),
        }
    )
    assert d.source == "django:http://d2"


def directory():
    from src.clients.discovery import parse

    return Directory(parse(PAYLOAD)[1], "cache")


def test_endpoints_local_first_then_allowed_in_order(tmp_path):
    lt = tmp_path / "local-token"
    lt.write_text("sha256:local\n")
    s = EdgeSettings(
        local=LocalUrls(llm="http://127.0.0.1:8080", asr=None, tts=None),
        local_token_file=str(lt),
    )
    eps = endpoints("llm", s, directory(), hostname="sktop")
    assert [(e.node, e.url, e.local) for e in eps] == [
        ("sktop", "http://127.0.0.1:8080", True),
        ("cyborg", "http://cyborg:8080", False),
    ]
    assert eps[0].credential.value == "t-l1"  # discovered token for the local node
    assert eps[0].credential.fallback_file == str(lt)
    assert eps[1].credential.value == "t-l2" and eps[1].credential.fallback_file is None


def test_endpoints_respect_allowed_services(tmp_path):
    s = EdgeSettings(
        local=LocalUrls(llm=None, asr=None, tts=None), allowed_services=("tts:cyborg",)
    )
    assert endpoints("llm", s, directory(), hostname="sktop") == []
    assert [e.node for e in endpoints("tts", s, directory(), hostname="sktop")] == [
        "cyborg"
    ]


def test_offline_local_worker_uses_local_token_file(tmp_path):
    lt = tmp_path / "local-token"
    lt.write_text("sha256:local\n")
    s = EdgeSettings(
        local=LocalUrls(llm="http://127.0.0.1:8080"), local_token_file=str(lt)
    )
    (e,) = endpoints("llm", s, Directory(), hostname="sktop")
    assert e.credential.value == "sha256:local" and e.credential.offline is True


def test_service_entry_key():
    assert ServiceEntry("asr", "n", "u", "t").key == "asr:n"
