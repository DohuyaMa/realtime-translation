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


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def rotating_django(tokens):
    """Django whose cyborg llm token changes on every call."""
    calls = []

    def handle(req):
        calls.append(1)
        body = json.loads(json.dumps(PAYLOAD))
        body["services"][1]["token"] = tokens[min(len(calls), len(tokens)) - 1]
        return httpx.Response(200, json=body)

    return handle, calls


def test_401_triggers_rate_limited_rediscovery_and_retry(tmp_path):
    from src.clients import ServiceClient

    handle, calls = rotating_django(["t-old", "t-new", "t-newer"])
    tok = tmp_path / "drf"
    tok.write_text("drf\n")
    s = EdgeSettings(
        django_urls=("http://d1",),
        django_token_file=str(tok),
        local=LocalUrls(asr=None, llm=None, tts=None),
    )
    d = discover(
        s, cache_path=tmp_path / "c.json", transport=httpx.MockTransport(handle)
    )
    clock = Clock()
    d.clock, d._last = clock, 0.0
    ep = endpoints("llm", s, d, hostname="sktop")[-1]  # cyborg (rotating token)
    assert ep.credential.value == "t-old"
    valid = {"t": "t-new"}
    seen = []

    def worker(req):
        seen.append(req.headers["authorization"])
        good = req.headers["authorization"] == f"Token {valid['t']}"
        return httpx.Response(200 if good else 401, json={})

    c = ServiceClient(
        ep.url, credential=ep.credential, transport=httpx.MockTransport(worker)
    )
    clock.t = 31.0
    assert c._request("GET", "/x").status_code == 200
    assert seen == ["Token t-old", "Token t-new"] and len(calls) == 2
    valid["t"] = "t-newer"  # rotated again within 30 s: no rediscovery
    clock.t = 40.0
    seen.clear()
    with pytest.raises(Exception) as e:
        c._request("GET", "/x")
    assert getattr(e.value, "kind", None) == "auth" and len(calls) == 2
    assert seen == ["Token t-new"]


def test_periodic_refresh_pushes_new_tokens(tmp_path):
    handle, calls = rotating_django(["t-old", "t-new"])
    tok = tmp_path / "drf"
    tok.write_text("drf\n")
    s = EdgeSettings(
        django_urls=("http://d1",),
        django_token_file=str(tok),
        local=LocalUrls(asr=None, llm=None, tts=None),
    )
    d = discover(
        s, cache_path=tmp_path / "c.json", transport=httpx.MockTransport(handle)
    )
    ep = endpoints("llm", s, d, hostname="sktop")[-1]
    d.min_refresh_s = 0.0
    d.start_periodic(0.02)
    try:
        import time

        deadline = time.monotonic() + 3
        while ep.credential.value != "t-new" and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        d.stop_periodic()
    assert ep.credential.value == "t-new"


def test_failed_refresh_keeps_current_list(tmp_path):
    d = Directory(
        [ServiceEntry("llm", "n", "u", "t")],
        "django:x",
        fetch=lambda: Directory([], "cache"),
        min_refresh_s=0.0,
    )
    assert d.refresh() is False and d.services[0].token == "t"


def _dir(*entries):
    return Directory([ServiceEntry(*e) for e in entries], "cache")


def _no_local(host):
    return False


def test_own_node_without_local_url_goes_via_loopback():
    s = EdgeSettings(local=LocalUrls(asr=None, llm=None, tts=None))
    d = _dir(
        ("asr", "SKTOP-NOTE27-NIXOS", "http://100.87.250.83:8091", "t-own"),
        ("asr", "cyborg", "http://100.69.30.96:8091", "t-c"),
    )
    eps = endpoints("asr", s, d, hostname="sktop-note27-nixos", local_address=_no_local)
    assert [(e.node, e.url, e.local) for e in eps] == [
        (
            "SKTOP-NOTE27-NIXOS",
            "http://127.0.0.1:8091",
            True,
        ),  # never its tailscale URL
        ("cyborg", "http://100.69.30.96:8091", False),
    ]
    assert eps[0].credential.value == "t-own"


def test_own_node_by_local_url_host_and_by_config_node():
    d = _dir(
        ("tts", "box", "http://100.69.30.96:8093", "t"),
        ("tts", "far", "http://100.1.1.1:8093", "t2"),
    )
    s = EdgeSettings(local=LocalUrls(asr=None, tts="http://127.0.0.1:9993"))
    eps = endpoints(
        "tts", s, d, hostname="other", local_address=lambda h: h == "100.69.30.96"
    )
    assert [(e.node, e.url) for e in eps] == [
        ("box", "http://127.0.0.1:9993"),  # explicit local.tts wins
        ("far", "http://100.1.1.1:8093"),
    ]
    s = EdgeSettings(local=LocalUrls(asr=None, tts=None, node="far"))
    eps = endpoints("tts", s, d, hostname="other", local_address=_no_local)
    assert [(e.node, e.url, e.local) for e in eps] == [
        ("far", "http://127.0.0.1:8093", True),
        ("box", "http://100.69.30.96:8093", False),
    ]


def test_disallowed_own_node_is_skipped():
    s = EdgeSettings(
        local=LocalUrls(asr="http://127.0.0.1:8091"), allowed_services=("asr:gpu",)
    )
    d = _dir(
        ("asr", "me", "http://me:8091", "t"), ("asr", "gpu", "http://gpu:8091", "t")
    )
    eps = endpoints("asr", s, d, hostname="me", local_address=_no_local)
    assert [e.node for e in eps] == ["gpu"]


def test_pinned_service_is_the_only_endpoint():
    from src.core.runtime import ServiceUse

    d = _dir(
        ("asr", "me", "http://100.0.0.1:8091", "t"),
        ("asr", "gpu", "http://gpu:8091", "t"),
        ("asr", "far", "http://far:8091", "t"),
    )
    base = EdgeSettings(local=LocalUrls(asr=None))
    auto = endpoints("asr", base, d, hostname="me", local_address=_no_local)
    assert [e.node for e in auto] == ["me", "gpu", "far"]
    pin_gpu = EdgeSettings(local=LocalUrls(asr=None), use=ServiceUse(asr="gpu"))
    assert [
        (e.node, e.url)
        for e in endpoints("asr", pin_gpu, d, hostname="me", local_address=_no_local)
    ] == [("gpu", "http://gpu:8091")]
    pin_me = EdgeSettings(local=LocalUrls(asr=None), use=ServiceUse(asr="me"))
    assert [
        (e.node, e.url)
        for e in endpoints("asr", pin_me, d, hostname="me", local_address=_no_local)
    ] == [("me", "http://127.0.0.1:8091")]
    gone = EdgeSettings(local=LocalUrls(asr=None), use=ServiceUse(asr="nope"))
    assert endpoints("asr", gone, d, hostname="me", local_address=_no_local) == []


def test_choices_ignore_pin_and_do_not_register():
    from src.clients.discovery import choices
    from src.core.runtime import ServiceUse

    d = _dir(
        ("asr", "me", "http://me:8091", "t"), ("asr", "gpu", "http://gpu:8091", "t")
    )
    s = EdgeSettings(local=LocalUrls(asr=None), use=ServiceUse(asr="gpu"))
    eps = choices("asr", s, d, hostname="me", local_address=_no_local)
    assert [e.node for e in eps] == ["me", "gpu"]
    assert d._creds == {}


def test_is_local_address_uses_host_addresses(monkeypatch):
    from src.clients import discovery

    monkeypatch.setattr(
        discovery, "host_addresses", lambda: frozenset({"100.69.30.96"})
    )
    assert discovery.is_local_address("100.69.30.96")
    assert discovery.is_local_address("127.0.0.1") and discovery.is_local_address(
        "localhost"
    )
    assert not discovery.is_local_address("100.64.0.1")
    assert not discovery.is_local_address("cyborg")


def test_loopback_substitution_is_logged_once_per_change():
    """endpoints() runs on every stats poll (~2x/s): the «is this host»
    line is logged once, again only when the URL changes."""
    from loguru import logger

    from src.clients import discovery

    discovery._loopback_logged.clear()
    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="INFO")
    try:
        s = EdgeSettings(local=LocalUrls(asr=None, llm=None, tts=None))
        d = _dir(("asr", "box", "http://100.1.1.1:8091", "t"))
        for _ in range(5):
            endpoints("asr", s, d, hostname="box", local_address=_no_local)
        d2 = _dir(("asr", "box", "http://100.1.1.1:9091", "t"))
        endpoints("asr", s, d2, hostname="box", local_address=_no_local)
    finally:
        logger.remove(sink)
    lines = [m for m in seen if "is this host" in m]
    assert len(lines) == 2 and "127.0.0.1:9091" in lines[1]
