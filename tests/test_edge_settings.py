from src.core.runtime import resolve_edge_settings


def test_auto_mode_derivation():
    assert resolve_edge_settings({}, {}).effective_mode() == "transcribe"
    lease = {"services": {"tts_urls": ["http://t"], "llm": {"django_urls": ["http://d"]}}}
    assert resolve_edge_settings(lease, {}).effective_mode() == "translate"
    direct = {"services": {"tts_urls": ["http://t"], "llm": {"direct_url": "http://l"}}}
    assert resolve_edge_settings(direct, {}).effective_mode() == "translate"
    assert resolve_edge_settings({"services": {"llm": {"direct_url": "http://l"}}}, {}).effective_mode() == "transcribe"


def test_env_lists_override_config():
    cfg = {"services": {"asr_urls": ["http://cfg:9"]}}
    s = resolve_edge_settings(cfg, {"RT_EDGE_ASR_URLS": "http://a:1,http://b:2/"})
    assert s.asr_urls == ("http://a:1", "http://b:2")
    assert "asr_urls" in s.managed


def test_config_used_when_env_absent_and_defaults():
    s = resolve_edge_settings({}, {})
    assert s.asr_urls == ("http://127.0.0.1:8091",)
    assert s.validate() == []
    s = resolve_edge_settings(
        {"services": {"llm": {"direct_url": "http://l/", "model": "m"}}}, {"RT_EDGE_LLM_MODEL": "x"}
    )
    assert s.llm.direct_url == "http://l" and s.llm.model == "x"


def test_lease_requires_django_token_file():
    s = resolve_edge_settings({"services": {"llm": {"django_urls": ["http://d"]}}}, {})
    assert s.validate()
    s = resolve_edge_settings({"services": {"llm": {"django_urls": ["http://d"], "django_token_file": "/t"}}}, {})
    assert s.validate() == []


def test_transcribe_chunk_max_over_900s_invalid():
    s = resolve_edge_settings({"segmentation": {"transcribe": {"chunk_max_s": 1200}}}, {})
    assert s.validate()


def test_translate_mode_requires_llm_and_tts():
    assert len(resolve_edge_settings({"services": {"mode": "translate"}}, {}).validate()) == 2
