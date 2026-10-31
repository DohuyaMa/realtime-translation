import pytest

from src.core.prompt import (
    DEFAULT_SYSTEM_PROMPT,
    build_messages,
    is_legacy,
    split_words,
    template_errors,
)
from src.core.runtime import LlmSettings, EdgeSettings, resolve_edge_settings


def test_template_validation():
    assert template_errors("t", "Say: {{text}} ({{source_lang}})", True) == []
    assert template_errors("t", "no words", True) == [
        "t must contain {{text}} (where the words go)"
    ]
    (err,) = template_errors("t", "{{text}} {{lang}}", True)
    assert "unknown placeholder(s) lang" in err
    assert any("old placeholder" in e for e in template_errors("t", "{text}", True))
    assert template_errors("s", DEFAULT_SYSTEM_PROMPT, False) == []
    assert is_legacy("x {lang}") and not is_legacy("x {{source_lang}}")


def test_settings_validate_templates_and_words():
    bad = EdgeSettings(llm=LlmSettings(user_template="hello", words_per_request=0))
    errors = bad.validate()
    assert any("must contain {{text}}" in e for e in errors)
    assert "llm.words_per_request must be >= 1" in errors


def test_legacy_config_warns_and_uses_default(caplog):
    s = resolve_edge_settings(
        {
            "services": {
                "llm": {"system_prompt": "Translate {lang}", "user_template": "{text}"}
            }
        },
        {},
    )
    assert s.llm.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert s.llm.user_template == "{{text}}"
    assert len(s.notices) == 2 and all("old {lang}/{text}" in n for n in s.notices)
    assert s.validate() == []


def test_split_words_prefers_sentence_ends():
    text = "One two three. Four five six seven. Eight."
    assert split_words(text, 40) == [text]
    assert split_words(text, 4) == ["One two three.", "Four five six seven.", "Eight."]
    assert split_words(text, 7) == ["One two three. Four five six seven.", "Eight."]
    # a sentence longer than the limit is cut on word boundaries
    assert split_words("a b c d e f g", 3) == ["a b c", "d e f", "g"]
    with pytest.raises(ValueError):
        split_words("x", 0)


def test_build_messages_renders_placeholders():
    msgs = build_messages(
        "From {{source_lang}} to {{target_lang}}.",
        "[{{text}}]",
        "cześć",
        "pl",
        (("a", "A"),),
    )
    assert msgs == [
        {"role": "system", "content": "From Polish to English."},
        {"role": "user", "content": "[a]"},
        {"role": "assistant", "content": "A"},
        {"role": "user", "content": "[cześć]"},
    ]


def test_llm_client_one_call_per_chunk_in_order():
    import json

    import httpx

    from src.clients.llm import LlmClient

    seen = []

    def handle(req):
        body = json.loads(req.content)
        seen.append(body["messages"])
        last = body["messages"][-1]["content"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": last.upper()}}]}
        )

    class Target:
        def current(self):
            return "http://w", "t"

        def invalidate(self, kind):
            pass

    client = LlmClient(
        Target(),
        LlmSettings(words_per_request=3, user_template="T: {{text}}"),
        transport=httpx.MockTransport(handle),
    )
    out = client.translate("raz dwa. trzy cztery pięć sześć", "pl")
    assert out == "T: RAZ DWA. T: TRZY CZTERY PIĘĆ T: SZEŚĆ"
    assert [m[-1]["content"] for m in seen] == [
        "T: raz dwa.",
        "T: trzy cztery pięć",
        "T: sześć",
    ]
    # earlier chunks of the phrase are context of the next ones
    assert seen[2][-3:-1] == [
        {"role": "user", "content": "T: trzy cztery pięć"},
        {"role": "assistant", "content": "T: TRZY CZTERY PIĘĆ"},
    ]


def test_asr_prompt_render_and_trim():
    from src.core.prompt import asr_prompt_errors, render_asr_prompt

    words = "a1 b2 c3 d4 e5".split()
    assert render_asr_prompt("{{previous_text}}", words, 3, None) == "c3 d4 e5"
    assert render_asr_prompt("{{previous_text}}", words, 0, None) is None
    assert render_asr_prompt("", words, 3, None) is None
    # no variables: as-is, previous words are not appended
    assert render_asr_prompt("Glossary: NixOS", words, 3, None) == "Glossary: NixOS"
    assert (
        render_asr_prompt("[{{source_lang}}] {{previous_text}}", words, 2, "pl")
        == "[Polish] d4 e5"
    )
    # over the limit: oldest whole words dropped, never cut mid-word
    long = ["słowo%02d" % i for i in range(100)]
    out = render_asr_prompt("X {{previous_text}}", long, 100, None, max_chars=40)
    assert len(out) <= 40 and out.endswith("słowo99")
    assert all(w in long for w in out.split()[1:])
    assert asr_prompt_errors("p", "{{text}}")[0].startswith("p: unknown placeholder")
    assert any("characters" in e for e in asr_prompt_errors("p", "x" * 400))


def test_asr_settings_resolve_and_validate():
    s = resolve_edge_settings(
        {"services": {"asr": {"prompt": "", "previous_words": 0}}}, {}
    )
    assert (s.asr.prompt, s.asr.previous_words) == ("", 0)
    bad = resolve_edge_settings(
        {"services": {"asr": {"prompt": "{{foo}}", "previous_words": -1}}}, {}
    )
    errors = bad.validate()
    assert any("asr.prompt: unknown placeholder(s) foo" in e for e in errors)
    assert "asr.previous_words must be >= 0" in errors
