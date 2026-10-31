from src.core.runtime import resolve_edge_settings


def test_auto_mode_derivation():
    assert resolve_edge_settings({}, {}).effective_mode() == "transcribe"
    local = {"services": {"local": {"llm": "http://l", "tts": "http://t"}}}
    assert resolve_edge_settings(local, {}).effective_mode() == "translate"
    django = {"services": {"django_urls": ["http://d"], "django_token_file": "/t"}}
    assert resolve_edge_settings(django, {}).effective_mode() == "translate"
    only_llm = {"services": {"local": {"llm": "http://l"}}}
    assert resolve_edge_settings(only_llm, {}).effective_mode() == "transcribe"


def test_env_overrides_config_and_marks_managed():
    cfg = {"services": {"local": {"asr": "http://cfg:9"}}}
    env = {
        "RT_EDGE_LOCAL_ASR_URL": "http://a:1/",
        "RT_EDGE_DJANGO_URLS": "http://d1,http://d2/",
        "RT_EDGE_ALLOWED_SERVICES": "llm:cyborg, tts:sktop",
        "RT_EDGE_LOCAL_TOKEN_FILE": "/run/x",
    }
    s = resolve_edge_settings(cfg, env)
    assert s.local.asr == "http://a:1"
    assert s.django_urls == ("http://d1", "http://d2")
    assert s.allowed_services == ("llm:cyborg", "tts:sktop")
    assert s.local_token_file == "/run/x"
    assert {"local.asr", "django_urls", "allowed_services"} <= s.managed


def test_defaults():
    s = resolve_edge_settings({}, {})
    assert s.local.asr == "http://127.0.0.1:8091"
    assert s.local.llm is None and s.local.tts is None
    assert s.local_token_file == "/run/llama/local-token"
    assert s.allowed_services is None
    assert s.validate() == []


def test_django_requires_token_file_and_allowed_format():
    s = resolve_edge_settings({"services": {"django_urls": ["http://d"]}}, {})
    assert any("django_token_file" in e for e in s.validate())
    s = resolve_edge_settings({"services": {"allowed_services": ["llm-cyborg"]}}, {})
    assert any("allowed_services" in e for e in s.validate())


def test_obsolete_keys_are_reported(caplog):
    from loguru import logger

    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        resolve_edge_settings(
            {"services": {"asr_urls": ["http://x"], "llm": {"django_urls": []}}}, {}
        )
    finally:
        logger.remove(sink)
    assert any("asr_urls" in m and "llm.django_urls" in m for m in seen)


def test_transcribe_chunk_max_over_900s_invalid():
    s = resolve_edge_settings(
        {"segmentation": {"transcribe": {"chunk_max_s": 1200}}}, {}
    )
    assert s.validate()


def test_translate_mode_requires_llm_and_tts():
    errors = resolve_edge_settings({"services": {"mode": "translate"}}, {}).validate()
    assert len(errors) == 2


def test_record_session_flag_from_config_and_env():
    import pytest

    from src.core.runtime import resolve_edge_settings

    assert resolve_edge_settings({}, {}).record_session is False
    on = resolve_edge_settings({"services": {"record_session": True}}, {})
    assert on.record_session is True and "record_session" not in on.managed
    env = resolve_edge_settings({}, {"RT_EDGE_RECORD_SESSION": "1"})
    assert env.record_session is True and "record_session" in env.managed
    with pytest.raises(ValueError):
        resolve_edge_settings({"services": {"record_session": "maybe"}}, {})
