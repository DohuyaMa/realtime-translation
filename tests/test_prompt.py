import pytest

from src.core.prompt import (
    DEFAULT_SYSTEM_PROMPT,
    build_messages,
    is_legacy,
    split_words,
    system_prompt_errors,
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
    assert system_prompt_errors("s", DEFAULT_SYSTEM_PROMPT) == []
    # {{terms}} is for system prompts only
    assert template_errors("s", "{{text}} {{terms}}", True)
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
    assert s.llm.user_template == "TEXT: {{text}}"
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
        last = body["messages"][-1]["content"].split("\n")[0]  # not NEXT
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
        "T: raz dwa.\nNEXT (do not translate): trzy cztery pięć sześć",
        "T: trzy cztery pięć\nNEXT (do not translate): sześć",
        "T: sześć",
    ]
    # earlier chunks of the phrase are context of the next ones
    assert seen[2][-3:-1] == [
        {"role": "user", "content": "T: trzy cztery pięć"},
        {"role": "assistant", "content": "T: TRZY CZTERY PIĘĆ"},
    ]


def test_asr_prompt_render():
    from src.core.prompt import asr_prompt_errors, render_asr_prompt

    assert render_asr_prompt("", "pl") is None
    assert render_asr_prompt("Glossary: NixOS", None) == "Glossary: NixOS"
    assert render_asr_prompt("[{{source_lang}}] NixOS", "pl") == "[Polish] NixOS"
    # rt-asr adds the committed text itself: no {{previous_text}} any more
    assert asr_prompt_errors("p", "{{previous_text}}")[0].startswith(
        "p: unknown placeholder"
    )
    assert any("characters" in e for e in asr_prompt_errors("p", "x" * 400))


def test_asr_settings_resolve_and_validate():
    s = resolve_edge_settings({"services": {"asr": {"prompt": ""}}}, {})
    assert s.asr.prompt == ""
    assert resolve_edge_settings({}, {}).asr.prompt == ""
    bad = resolve_edge_settings({"services": {"asr": {"prompt": "{{foo}}"}}}, {})
    errors = bad.validate()
    assert any("asr.prompt: unknown placeholder(s) foo" in e for e in errors)


def test_default_system_prompt_forbids_untranslated_words():
    # live test 2026-10-31 §2-§4: "Chikaie DOKI", "Promd", ("Niшки")
    from src.core.prompt import DEFAULT_SYSTEM_PROMPT, build_messages

    text = build_messages(DEFAULT_SYSTEM_PROMPT, "{{text}}", "x", "uk")[0]["content"]
    assert "Never keep or transliterate a source word" in text
    assert "no Ukrainian words or letters" in text
    assert "{{" not in text


# -- NEXT line (spec 2026-10-31 §4) and the LLM glossary {{terms}} ----------


def test_next_line_only_in_the_last_message():
    from src.core.prompt import DEFAULT_SYSTEM_PROMPT, NEXT_LABEL, build_messages

    msgs = build_messages(
        DEFAULT_SYSTEM_PROMPT,
        "TEXT: {{text}}",
        "Перше речення.",
        "uk",
        context=(("Раніше.", "Earlier."),),
        next_text="  друге без ",
    )
    assert msgs[1] == {"role": "user", "content": "TEXT: Раніше."}
    assert msgs[-1]["content"] == f"TEXT: Перше речення.\n{NEXT_LABEL}друге без"
    assert "NEXT" in msgs[0]["content"] and "never translate NEXT" in msgs[0]["content"]
    bare = build_messages(DEFAULT_SYSTEM_PROMPT, "TEXT: {{text}}", "x", "uk")
    assert bare[-1]["content"] == "TEXT: x"


def test_terms_render_only_when_present():
    from src.core.prompt import DEFAULT_SOUND_SYSTEM_PROMPT, parse_terms

    assert parse_terms("CRM, SaaS\nPlaywright,, crm , CS2") == (
        "CRM",
        "SaaS",
        "Playwright",
        "CS2",
    )
    # the sound prompt names its own examples (tuned by replay, no IT_DOMAIN)
    for prompt, end in (
        (DEFAULT_SYSTEM_PROMPT, "English term."),
        (DEFAULT_SOUND_SYSTEM_PROMPT, "Output only the English translation."),
    ):
        with_terms = build_messages(prompt, "{{text}}", "x", "uk", terms=("CRM",))
        assert with_terms[0]["content"].endswith(
            "Known terms (always written in English): CRM."
        )
        bare = build_messages(prompt, "{{text}}", "x", "uk")[0]["content"]
        assert "{{" not in bare and "Known terms" not in bare
        assert bare.endswith(end)
    assert (
        "about IT"
        in build_messages(DEFAULT_SYSTEM_PROMPT, "{{text}}", "x", "uk")[0]["content"]
    )
    mid = build_messages("A {{terms}} B\n{{terms}}\nC", "{{text}}", "x", "uk")
    assert mid[0]["content"] == "A B\n\nC"


def test_terms_config_and_validation():
    from src.core.prompt import DEFAULT_TERMS

    def terms(cfg):
        return resolve_edge_settings({"services": {"llm": cfg}}, {}).llm.terms

    assert terms({}) == DEFAULT_TERMS and "Playwright" in DEFAULT_TERMS
    assert terms({"terms": ""}) == ()
    assert terms({"terms": ["CRM", "SaaS"]}) == ("CRM", "SaaS")
    assert terms({"terms": "CRM, медіабаєри"}) == ("CRM", "медіабаєри")
    s = EdgeSettings(llm=LlmSettings(user_template="{{text}} {{terms}}"))
    assert any("terms" in e for e in s.validate())


def test_retry_note_is_the_last_line_of_the_last_message_only():
    from src.core.prompt import COMPLETE_RETRY_NOTE, NEXT_LABEL

    msgs = build_messages(
        DEFAULT_SYSTEM_PROMPT,
        "TEXT: {{text}}",
        "раз два",
        "uk",
        context=(("Раніше.", "Earlier."),),
        next_text="три",
        insist_complete=True,
    )
    assert msgs[1] == {"role": "user", "content": "TEXT: Раніше."}
    assert msgs[-1]["content"] == (
        f"TEXT: раз два\n{NEXT_LABEL}три\n{COMPLETE_RETRY_NOTE}"
    )
    assert COMPLETE_RETRY_NOTE not in msgs[0]["content"]


def test_sound_prompt_takes_english_terms_from_text_and_names_no_machines():
    """Owner 2026-11-01: the phonetic model is Ukrainian-only, so Latin-script
    TEXT words are kept verbatim and "trust SOUND" is for Ukrainian words;
    the owner's own machine names (Cyborg, SKTOP) belong in the glossary."""
    from src.core.prompt import DEFAULT_SOUND_SYSTEM_PROMPT

    p = DEFAULT_SOUND_SYSTEM_PROMPT
    assert "English terms come from TEXT, never from SOUND" in p
    assert "Latin letters" in p and "Zero Trust" in p
    assert "trust SOUND" in p
    for personal in ("сайборг", "Cyborg", "SKTOP", "ес кей топ"):
        assert personal not in p
