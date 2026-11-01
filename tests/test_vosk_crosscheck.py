"""TASK-SYS-12: whisper + Vosk cross-check in the streaming translate
pipeline - the per-unit gate (Vosk covers the unit's end within
VOSK_WAIT_S, else whisper-only), two-text LLM messages, Vosk off /
unavailable / of another language visible, rt-vosk in the kill switch."""

import dataclasses
import json
import time

import httpx
import numpy as np
import pytest

from src.clients import Directory, ServiceEntry, ServiceError, build_pools
from src.clients.discovery import endpoints
from src.clients.llm import LlmClient
from src.clients.vosk import VoskClient, VoskResult, VoskWord
from src.core.crosscheck import (
    GATE_AGREE,
    GATE_DISAGREE,
    GATE_FOREIGN_TERMS,
    GATE_NO_WORD,
    GATE_SHORT_WORDS,
    GATE_SPARSE,
    GATE_UNCOVERED,
    Aligned,
    agreement,
    gate_reason,
    has_real_word,
    mean_conf,
)
from src.core.prompt import (
    DEFAULT_CHECK_SYSTEM_PROMPT,
    build_messages,
    check_template_errors,
)
from src.core.runtime import (
    OFF,
    EdgeSettings,
    LlmSettings,
    LocalUrls,
    ServiceUse,
    resolve_edge_settings,
)
from src.core.simul import Word
from src.translation_system import VOSK_WAIT_S, TranslationSystem
from test_pipeline_stages import SlowLlm, SlowTts
from test_translation_system import (
    FakeAsr,
    FakeCapture,
    FakeModelService,
    FakePools,
    FakeStreams,
    StaticTarget,
    final,
    wait_for,
)


def words(text, start=0.0, step=0.2):
    return [
        Word(w, start + i * step, start + (i + 1) * step)
        for i, w in enumerate(text.split())
    ]


class CheckLlm(SlowLlm):
    """Records (whisper text, Vosk check text or None, NEXT)."""

    def __init__(self):
        super().__init__(0)
        self.checks = []
        self.nexts = []

    def translate_stream(self, text, source_lang, context=(), check=None, next_text=""):
        self.calls.append(text)
        self.checks.append(None if check is None else check.check_text)
        self.nexts.append(next_text)
        yield f"EN{len(self.calls)}."


def settings(**kw):
    local = LocalUrls(llm="http://llm", tts="http://tts")
    kw.setdefault("source_language", "uk")
    return EdgeSettings(mode="translate", local=local, **kw)


def system(llm=None, streams=None, vosk=True, **kw):
    """(ts, streams, llm, events); ``vosk`` = rt-vosk is configured."""
    capture, events = FakeCapture(), []
    llm = llm or CheckLlm()
    streams = streams or FakeStreams()
    vosk_client = FakeModelService() if vosk else None
    ts = TranslationSystem(
        settings(**kw),
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: FakePools(FakeAsr(), SlowTts(0), vosk_client),
        llm_target_factory=lambda s, d: StaticTarget("http://llm"),
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=lambda a, sr: None,
        stream_factory=streams,
    )
    ts.set_status_callback(events.append)
    return ts, streams, llm, events


def vosk_final(text, start, end, language="uk", eos=True):
    ws = text.split()
    step = (end - start) / max(1, len(ws))
    return {
        "type": "final",
        "text": text,
        "words": [
            {
                "word": w,
                "start": start + i * step,
                "end": start + (i + 1) * step,
                "conf": 1.0,
            }
            for i, w in enumerate(ws)
        ],
        "start": start,
        "end": end,
        "eos": eos,
        "language": language,
        "audio_end": end + 0.5,
    }


def speak(streams, whisper, vosk=None, start=0.0, language="uk", eos=True):
    """One utterance: Vosk's final over the same span first (when given),
    then whisper's; returns where the next one may start."""
    msg = final(whisper, start, eos=eos, language=language)
    if vosk is not None:
        streams["vosk"].emit(vosk_final(vosk, start, msg["end"], language))
    streams["asr"].emit(msg)
    return msg["end"] + 0.5


def run(ts, streams, utterances, until):
    """utterances: (whisper, vosk text or None) in order."""
    assert ts.start()
    try:
        t = 0.0
        for whisper, vosk in utterances:
            t = speak(streams, whisper, vosk, t)
        assert wait_for(until)
        return ts.get_stats()
    finally:
        ts.stop()


# -- pure helpers ---------------------------------------------------------


def test_gate_needs_a_word_of_two_letters():
    assert not has_real_word("")
    assert not has_real_word("е і а")
    assert has_real_word("е до")


def test_aligned_from_words_cuts_vosk_words_by_time():
    a = Aligned.from_words(
        [Word("Раз", 0.0, 0.5), Word("два.", 0.5, 1.0), Word("Три", 1.0, 2.0)],
        words("раз два три", 0.0, 0.66),
    )
    head, tail = a.cut(2)
    assert head.check_text == "раз два" and tail.check_text == "три"
    assert (head + tail).check_text == "раз два три"


# -- prompt / settings ------------------------------------------------------


def test_check_messages_carry_both_texts_only_in_the_user_message():
    s = LlmSettings()
    msgs = build_messages(
        s.check_system_prompt, s.check_user_template, "Дякую.", "uk", check="дякую"
    )
    assert msgs[0]["content"].startswith(
        "You are a live Ukrainian-to-English interpreter."
    )
    assert "{{" not in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "TEXT: Дякую.\nHINT: дякую"}


def test_check_template_validation():
    assert check_template_errors("t", "EXACT: {{vosk_text}} {{text}}", True) == []
    assert check_template_errors("t", "{{text}}", True)  # no {{vosk_text}}
    assert check_template_errors("s", DEFAULT_CHECK_SYSTEM_PROMPT, False) == []
    bad = dataclasses.replace(
        settings(), llm=LlmSettings(user_template="{{text}} {{vosk_text}}")
    )
    assert any("vosk_text" in e for e in bad.validate())


def test_config_keeps_defaults_and_parses_vosk_use():
    s = resolve_edge_settings(
        {"services": {"use": {"vosk": "off"}, "llm": {"check_user_template": ""}}},
        {},
    )
    assert s.use.vosk == OFF
    assert s.llm.check_user_template == LlmSettings().check_user_template
    assert not [e for e in s.validate() if "vosk" in e]
    asr_off = dataclasses.replace(s, use=ServiceUse(asr=OFF))
    assert "use.asr: asr cannot be switched off" in asr_off.validate()


def test_llm_client_sends_check_prompts_per_chunk_with_next():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        n = len(bodies)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"T{n}."}}]}
        )

    client = LlmClient(
        StaticTarget("http://llm"),
        LlmSettings(words_per_request=2),
        transport=httpx.MockTransport(handler),
    )
    check = Aligned.from_words(
        [Word("Раз", 0, 1), Word("два", 1, 2), Word("три", 2, 3)],
        words("раз два три", 0.0, 1.0),
    )
    out = client.translate(
        "Раз два три", "uk", [("x", "X")], check=check, next_text="чотири"
    )
    assert out == "T1. T2."
    first, second = (b["messages"] for b in bodies)
    assert first[0]["content"].startswith("You are a live Ukrainian")
    # whisper-only context pairs are not shown in two-text mode; the rest of
    # the unit and the speech after it are NEXT
    assert first[1:] == [
        {
            "role": "user",
            "content": "TEXT: Раз два\nHINT: раз два\nNEXT (do not translate): три"
            " чотири",
        }
    ]
    assert second[1:] == [
        {"role": "user", "content": "TEXT: Раз два\nHINT: раз два"},
        {"role": "assistant", "content": "T1."},
        {
            "role": "user",
            "content": "TEXT: три\nHINT: три\nNEXT (do not translate): чотири",
        },
    ]


def test_vosk_client_request_and_words():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(
            200,
            json={
                "text": "привіт",
                "language": "uk",
                "words": [{"word": "привіт", "conf": 0.9, "start": 0.1, "end": 0.5}],
            },
        )

    c = VoskClient("http://v:8092", transport=httpx.MockTransport(handler))
    r = c.transcribe(np.ones(1600, dtype=np.int16), language="uk")
    assert seen["url"] == "http://v:8092/v1/audio/transcriptions"
    assert b'name="language"' in seen["body"] and b"uk" in seen["body"]
    assert r == VoskResult("привіт", "uk", (VoskWord("привіт", 0.9, 0.1, 0.5),))


# -- live test §12: Latin terms against a uk Vosk model (rt-app log 21:15:07)

S19 = (
    "CPress, Stack, TypeScript, React, SGS, NodeJS, Python, PostgreSQL, Redis,"
    " Docker..."
)
V19 = "єс поступ із доки"


# -- pipeline ---------------------------------------------------------------


def test_unit_carries_the_vosk_words_of_its_span():
    ts, streams, llm, _ = system()
    stats = run(ts, streams, [("Добрий день.", "добрий день")], lambda: llm.calls)
    assert streams["vosk"].language == "uk"
    assert llm.checks == ["добрий день"]
    assert stats["vosk_checked"] == 1 and stats["vosk"] == {"mode": "on", "error": None}


def test_unit_waits_for_vosk_to_cover_its_end():
    ts, streams, llm, _ = system()
    assert ts.start()
    try:
        msg = final("Добрий день.", 0.0)
        streams["asr"].emit(msg)  # whisper first: the unit waits for Vosk
        time.sleep(VOSK_WAIT_S / 3)
        assert llm.calls == [] and ts.get_stats()["queues"]["units"] == 1
        streams["vosk"].emit(vosk_final("добрий день", 0.0, msg["end"]))
        assert wait_for(lambda: llm.calls)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.checks == ["добрий день"] and stats["vosk_late"] == 0


def test_vosk_late_is_whisper_only_and_counted():
    ts, streams, llm, _ = system()
    stats = run(ts, streams, [("Добрий день.", None)], lambda: llm.calls)
    assert llm.checks == [None]
    assert stats["vosk_late"] == 1 and stats["vosk"]["mode"] == "on"


def test_deaf_vosk_turns_the_check_off_until_words_return():
    """§13: a quiet microphone - Vosk hears nothing while whisper does; the
    first silent unit is still gated, the second turns the check off (the
    unit passes whisper-only), Vosk words switch it back on."""
    ts, streams, llm, events = system()
    assert ts.start()
    try:
        t = speak(streams, "Давай спробуємо щось перевести.", None)
        # Vosk heard noise later on: covers the span, but no word in it
        streams["vosk"].emit(vosk_final("а", t + 5.0, t + 5.2))
        t = speak(streams, "Знову нічого не чути вслух.", None, t)
        t = speak(streams, "І ще одна фраза тут.", None, t)
        assert wait_for(lambda: len(llm.calls) == 2)
        mid = ts.get_stats()
        streams["vosk"].emit(vosk_final("так", t + 1.0, t + 1.3))
        speak(streams, "Це дуже добре.", "це дуже добре", t + 6.0)
        assert wait_for(lambda: len(llm.calls) == 3)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert mid["vosk"]["mode"] == "deaf"
    assert llm.calls[0] == "Знову нічого не чути вслух."  # 1st: no_word drop
    assert llm.checks == [None, None, "це дуже добре"]
    assert stats["vosk"]["mode"] == "on"
    states = [e["vosk"]["mode"] for e in events if e.get("status") == "vosk_state"]
    assert states == ["deaf", "on"]


def test_deaf_vosk_is_described_for_the_log():
    from src.ui.controller.ui_controller import describe_pipeline_event

    level, msg = describe_pipeline_event(
        {"status": "vosk_state", "vosk": {"mode": "deaf", "error": "x"}}
    )
    assert level == "WARN" and msg.startswith("Vosk не чує")


def test_unit_hint_is_cut_from_one_vosk_final_by_time():
    ts, streams, llm, _ = system()
    assert ts.start()
    try:
        vosk = vosk_final("не витрачайте час", 0.0, 0.9)
        vosk["words"] += [
            {"word": "картки", "start": 1.3, "end": 1.6, "conf": 1.0},
            {"word": "без", "start": 1.6, "end": 1.9, "conf": 1.0},
        ]
        vosk["audio_end"] = 2.4
        streams["vosk"].emit(vosk)
        # a short pause after the sentence; the sentence end cuts a unit
        streams["asr"].emit(final("Не витрачайте час.", 0.0, eos=False))
        streams["asr"].emit(final("Картки без", 1.3, eos=True))
        assert wait_for(lambda: len(llm.calls) == 2)
    finally:
        ts.stop()
    assert llm.calls == ["Не витрачайте час.", "Картки без"]
    assert llm.checks == ["не витрачайте час", "картки без"]


def test_gate_drops_unit_before_the_llm():
    ts, streams, llm, events = system()
    stats = run(
        ts,
        streams,
        [("Дякую за перегляд!", "е"), ("Добрий день.", "добрий день")],
        lambda: llm.calls,
    )
    assert llm.calls == ["Добрий день."]
    assert stats["vosk_gate_dropped"] == 1
    assert any(
        e.get("status") == "vosk_gate" and e["original_text"] == "Дякую за перегляд!"
        for e in events
    )


def test_gate_drops_disagreeing_unit_and_counts_it():
    ts, streams, llm, events = system()
    stats = run(
        ts,
        streams,
        [("Дякую за перегляд!", "що можна зробити"), ("Добрий день.", "добрий день")],
        lambda: llm.calls,
    )
    assert llm.calls == ["Добрий день."]
    assert stats["vosk_gate_dropped"] == 1 and stats["vosk_gate_disagree"] == 1
    assert any(
        e.get("status") == "vosk_gate" and e["reason"] == GATE_DISAGREE for e in events
    )


def test_sparse_vosk_keeps_the_whole_whisper_text():
    text = "Запитайте про відео або аудіо яке ви відтворювали."
    ts, streams, llm, _ = system()
    stats = run(ts, streams, [(text, "або")], lambda: llm.calls)
    assert llm.calls == [text] and llm.checks == ["або"]
    assert stats["vosk_gate_dropped"] == 0


def test_terms_unit_passes_as_foreign_terms():
    """§12 #19: the stack list against a uk model - no longer dropped."""
    ts, streams, llm, _ = system()
    stats = run(ts, streams, [(S19, V19)], lambda: llm.calls)
    assert llm.calls == [S19] and llm.checks == [V19]
    assert stats["vosk_gate_foreign_terms"] == 1 and stats["vosk_gate_dropped"] == 0


def test_vosk_off_is_whisper_only_and_visible():
    ts, streams, llm, _ = system(vosk=False, use=ServiceUse(vosk=OFF))
    stats = run(ts, streams, [("Добрий день.", None)], lambda: llm.calls)
    assert [s.kind for s in streams.opened] == ["asr"]
    assert llm.checks == [None]
    assert stats["vosk"] == {"mode": "off", "error": "вимкнено в налаштуваннях"}
    assert stats["vosk_whisper_only"] == 0  # a configured mode, not a problem


def test_vosk_not_found_is_visible():
    ts, streams, llm, _ = system(vosk=False)
    stats = run(ts, streams, [("Добрий день.", None)], lambda: llm.calls)
    assert llm.checks == [None]
    assert stats["vosk"]["mode"] == "unavailable"
    assert stats["vosk_whisper_only"] == 1


def test_vosk_stream_down_goes_whisper_only_then_back_on():
    ts, streams, llm, events = system()
    assert ts.start()
    try:
        streams["vosk"].emit({"type": "down", "url": "http://vosk", "error": "x"})
        t = speak(streams, "Раз.", None)
        assert wait_for(lambda: len(llm.calls) == 1)
        streams["vosk"].emit({"type": "ready", "url": "http://vosk", "reconnect": True})
        speak(streams, "Два.", "два", t)
        assert wait_for(lambda: len(llm.calls) == 2)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.checks == [None, "два"]
    assert stats["vosk_whisper_only"] == 1 and stats["vosk"]["mode"] == "on"
    states = [e["vosk"]["mode"] for e in events if e.get("status") == "vosk_state"]
    assert states == ["unavailable", "on"]


def test_auto_language_vosk_model_of_another_language_is_closed():
    """Auto: whisper fixes the language on the first words; a Vosk model of
    another language cannot check it - its stream closes, whisper-only."""
    ts, streams, llm, _ = system(source_language="auto")
    assert ts.start()
    try:
        assert streams["vosk"].language is None  # rt-vosk: its model's language
        streams["vosk"].emit(vosk_final("добрий день", 0.0, 0.6, language="uk"))
        streams["asr"].emit(final("Dzień dobry.", 0.0, language="pl"))
        assert wait_for(lambda: llm.calls)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert streams["vosk"].stopped
    assert stats["vosk"]["mode"] == "unavailable"
    assert stats["vosk"]["error"].startswith("немає моделі для «pl»")
    assert llm.checks == [None]


def test_wrong_language_model_is_visible_and_never_retried():
    """rt-vosk holds one model; 409 model_language_mismatch -> whisper-only,
    the reason names the language; the model is never switched by the edge."""
    import httpx as _httpx

    from src.clients.base import raise_for_response

    resp = _httpx.Response(
        409,
        json={
            "error": {
                "code": "model_language_mismatch",
                "message": "loaded Vosk model vosk-small-uk-v3-nano is 'uk', not 'pl'",
            }
        },
    )
    with pytest.raises(ServiceError) as exc:
        raise_for_response("vosk", resp)
    err = exc.value
    assert err.kind == "wrong_language" and err.status_code == 409
    from src.clients.pool import is_retryable

    assert not is_retryable(err)
    # the stream refuses the start the same way
    ts, streams, llm, _ = system(source_language="pl")
    assert ts.start()
    try:
        streams["vosk"].emit(
            {
                "type": "down",
                "url": "http://vosk",
                "error": "model_language_mismatch: loaded model is 'uk', not 'pl'",
            }
        )
        streams["asr"].emit(final("Dzień dobry.", 0.0, language="pl"))
        assert wait_for(lambda: llm.calls)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.checks == [None] and streams["vosk"].stopped
    assert stats["vosk"]["mode"] == "unavailable"
    assert stats["vosk"]["error"].startswith("немає моделі для «pl»")
    assert stats["pending_recognized"] == [{"text": "Dzień dobry.", "check": None}]


def test_recognized_unit_carries_vosk_text():
    ts, streams, llm, _ = system()
    stats = run(ts, streams, [("Добрий день.", "добрий день")], lambda: llm.calls)
    assert stats["pending_recognized"] == [
        {"text": "Добрий день.", "check": "добрий день"}
    ]


def test_mean_conf():
    assert mean_conf(()) is None
    assert mean_conf((VoskWord("а", 0.5, 0, 1), VoskWord("б", 1.0, 1, 2))) == 0.75


# -- discovery / pools / kill switch -----------------------------------------

CLUSTER = [
    ServiceEntry("vosk", "me", "http://100.1.1.1:8092", "tv"),
    ServiceEntry("vosk", "other", "http://other:8092", "to"),
]


@pytest.mark.parametrize(
    "use, urls",
    [
        (None, ["http://127.0.0.1:8092", "http://other:8092"]),
        ("other", ["http://other:8092"]),
        (OFF, []),
    ],
)
def test_vosk_endpoints_follow_use(use, urls):
    s = EdgeSettings(local=LocalUrls(node="me"), use=ServiceUse(vosk=use))
    d = Directory(list(CLUSTER), "cache")
    assert [e.url for e in endpoints("vosk", s, d)] == urls
    pools = build_pools(s, d)
    try:
        assert (pools.vosk is None) == (not urls)
        assert ("vosk" in pools.by_name()) == bool(urls)
    finally:
        pools.close()


@pytest.mark.parametrize("use, has", [(None, True), (OFF, False)])
def test_kill_switch_clients_include_local_vosk(use, has):
    s = EdgeSettings(
        mode="transcribe", local=LocalUrls(node="me"), use=ServiceUse(vosk=use)
    )
    ts = TranslationSystem(
        s,
        directory_factory=lambda _s: Directory(list(CLUSTER), "cache"),
        llm_target_factory=lambda _s, _d: None,
    )
    clients = ts.local_model_clients()
    try:
        assert ("vosk" in clients) == has
        if has:
            assert clients["vosk"].base_url == "http://127.0.0.1:8092"
    finally:
        for c in clients.values():
            c.close()
        ts.cleanup()


def test_vosk_events_are_described_for_the_log():
    from src.ui.controller.ui_controller import describe_pipeline_event

    level, msg = describe_pipeline_event(
        {"status": "vosk_gate", "original_text": "Дякую!", "check_text": "е"}
    )
    assert level == "INFO" and "Дякую!" in msg
    level, msg = describe_pipeline_event(
        {"status": "vosk_state", "vosk": {"mode": "unavailable", "error": "down"}}
    )
    assert level == "WARN" and "лише з whisper" in msg


# -- the gate: veto hallucinations, never delete real words (§10) ----------

# the owner's live whisper lines (2026-10-31 20:27) and what rt-vosk with
# vosk-small-uk-v3-nano heard on espeak-ng audio of the same text
OWNER_LINES = [
    (
        "Запитайте про відео або аудіо, яке ви відтворювали.",
        "відео або аудіо сповнювали",
    ),
    (
        "Ви можете ставити запитання про те, що почули у відео і аудіо."
        " У геніві хром відповідатиме на них.",
        "хліба та ставити питання про те що почули звук відео аудіо а біля"
        " і відповідатиме реальний",
    ),
    (
        "Попросіть надати докладну інформацію або підсумки, щоб краще"
        " зрозуміти прислуханий контент.",
        "попросив надати докладну інформацію або підсумки щоб краще зрозуміти"
        " уяві фонтенбло",
    ),
    # sparse Vosk: one shared word is enough to keep the whole phrase
    ("Запитайте про відео або аудіо, яке ви відтворювали.", "або"),
]
HALLUCINATIONS = [
    ("Дякую за перегляд!", "що можна зробити із віспером щоб він не галюцинував"),
    ("Субтитри зроблені спільнотою Amara.org", "відкрийте файл пдф і натисніть"),
    ("Продовження в наступній серії.", "запитайте про відео або аудіо яке в лівії"),
]


@pytest.mark.parametrize("whisper,vosk", OWNER_LINES)
def test_gate_keeps_real_speech_with_garbled_vosk(whisper, vosk):
    assert gate_reason(whisper, vosk, "uk") == GATE_AGREE
    assert agreement(whisper, vosk, "uk") > 0


@pytest.mark.parametrize("whisper,vosk", HALLUCINATIONS)
def test_gate_drops_whisper_phrase_vosk_disagrees_with(whisper, vosk):
    assert gate_reason(whisper, vosk, "uk") == GATE_DISAGREE
    assert agreement(whisper, vosk, "uk") == 0.0


def test_gate_silence_and_short_words():
    assert gate_reason("Дякую за перегляд!", "е", "uk") == GATE_NO_WORD
    # whisper without a word of >= 3 letters: no evidence, kept
    assert agreement("Ну, ок.", "та", "uk") is None
    assert gate_reason("Ну, ок.", "та", "uk") == GATE_SHORT_WORDS
    # split Vosk words still agree («інформа ції»)
    assert agreement("Інформації.", "інформа ції", "uk") == 1.0


# live 2026-11-01 §14: real phrases dropped on a single (garbled) Vosk word
SPARSE_VOSK = [
    ("Ці повинніше.", "буде"),
    ("Чи повільніше.", "ці"),
    ("Або повільніше.", "та"),
    ("Добре, зараз, щось зачитать.", "резонанс"),
    ("що почули у відео аудио.", "щоб"),
    ("Дякую!", "так"),
]


def test_exact_short_word_counts_as_agreement():
    # was: agree 0.00 -> gate disagree -> dropped («як» has 2 letters)
    assert agreement("як відчуття", "як", "uk") == pytest.approx(0.2)
    assert gate_reason("як відчуття", "як", "uk") == GATE_AGREE
    # a 2-letter word only by an exact match, never fuzzily
    assert agreement("Ну, ок.", "та", "uk") is None


@pytest.mark.parametrize("whisper,vosk", SPARSE_VOSK)
def test_one_vosk_word_is_no_veto(whisper, vosk):
    assert agreement(whisper, vosk, "uk") == 0.0
    assert gate_reason(whisper, vosk, "uk") == GATE_SPARSE


def test_partial_hint_short_of_the_unit_end_is_no_veto():
    text = "Добре, зараз, щось зачитать."
    assert gate_reason(text, "резонанс тут знову", "uk") == GATE_DISAGREE
    assert gate_reason(text, "резонанс тут знову", "uk", covered=False) == (
        GATE_UNCOVERED
    )
    assert gate_reason(text, "е", "uk", covered=False) == GATE_UNCOVERED
    assert gate_reason(text, "", "uk", covered=False) == GATE_UNCOVERED


def idle(at):
    return {"type": "idle", "at": at, "audio_end": at}


def test_whisper_in_a_pause_vosk_idle_past_it_is_dropped():
    """§14: «Дякую.» (59.67-60.79 s) and «знову.» in silence - Vosk's last
    word ended at 45.21 s, Kaldi idle since: no_word, not "Vosk late"."""
    ts, streams, llm, events = system()
    assert ts.start()
    try:
        speak(streams, "Або повільніше.", "або повільніше", 43.2)
        assert wait_for(lambda: len(llm.calls) == 1)
        for at in (50.0, 58.0, 61.5, 62.0):
            streams["vosk"].emit(idle(at))
        streams["asr"].emit(final("Дякую.", 59.67, step=1.12))
        streams["vosk"].emit(idle(73.0))
        streams["asr"].emit(final("знову.", 70.49, step=1.26))
        speak(streams, "Це дуже добре.", "це дуже добре", 80.0)
        assert wait_for(lambda: len(llm.calls) == 2)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.calls == ["Або повільніше.", "Це дуже добре."]
    assert stats["vosk_gate_dropped"] == 2 and stats["vosk_late"] == 0
    drops = [e for e in events if e.get("status") == "vosk_gate"]
    assert [(e["original_text"], e["reason"]) for e in drops] == [
        ("Дякую.", GATE_NO_WORD),
        ("знову.", GATE_NO_WORD),
    ]


def test_without_idle_a_pause_is_still_vosk_late():
    """Kaldi not idle (busy / an old rt-vosk): no evidence, whisper-only."""
    ts, streams, llm, _ = system()
    assert ts.start()
    try:
        streams["vosk"].emit(vosk_final("або", 44.8, 45.21))
        streams["vosk"].emit(idle(60.0))  # idle, but not 1 s past the end
        streams["asr"].emit(final("Дякую.", 59.67, step=1.12))
        assert wait_for(lambda: llm.calls)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.calls == ["Дякую."] and llm.checks == [None]
    assert stats["vosk_late"] == 1 and stats["vosk_gate_dropped"] == 0


def test_idle_vosk_with_real_speech_still_turns_deaf():
    """Laptop mic: Kaldi idle all along while whisper hears whole phrases -
    the first is gated, then Vosk is deaf and units go whisper-only."""
    ts, streams, llm, _ = system()
    assert ts.start()
    try:
        streams["vosk"].emit(idle(30.0))
        t = speak(streams, "Давай спробуємо щось перевести.", None)
        t = speak(streams, "Знову нічого не чути вслух.", None, t)
        speak(streams, "І ще одна фраза тут.", None, t)
        assert wait_for(lambda: len(llm.calls) == 2)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert stats["vosk"]["mode"] == "deaf"
    assert llm.calls == ["Знову нічого не чути вслух.", "І ще одна фраза тут."]


def test_partial_hint_never_drops_the_unit():
    """Vosk's partial words cover only the start of the unit: no veto."""
    ts, streams, llm, _ = system()
    assert ts.start()
    try:
        text = "Добре, зараз, щось зачитать."
        streams["vosk"].emit(
            {
                "type": "partial",
                "words": [
                    {"word": "резонанс", "start": 0.0, "end": 0.3, "conf": 0.6},
                    {"word": "тут", "start": 0.3, "end": 0.5, "conf": 0.6},
                ],
                "audio_end": 0.6,
            }
        )
        streams["asr"].emit(final(text, 0.0))
        assert wait_for(lambda: llm.calls)
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert llm.calls == [text] and llm.checks == ["резонанс тут"]
    assert stats["vosk_gate_dropped"] == 0


# -- session recording and per-unit log ------------------------------------


def _recording_system(tmp_path, **kw):
    from src.core.session_record import SessionRecorder

    ts, streams, llm, events = system(**kw)
    ts._recorder_factory = lambda: SessionRecorder(tmp_path)
    return ts, streams, llm, events


def test_session_recording_writes_wav_and_jsonl(tmp_path):
    ts, streams, llm, events = _recording_system(tmp_path, record_session=True)
    stats = run(
        ts,
        streams,
        [("Дякую за перегляд!", "що можна зробити"), ("Добрий день.", "добрий день")],
        lambda: llm.calls,
    )
    (session,) = list(tmp_path.iterdir())
    assert sorted(p.name for p in session.glob("*.wav")) == [
        "seg-00001.wav",
        "seg-00002.wav",
    ]
    lines = [
        json.loads(x) for x in (session / "phrases.jsonl").read_text().splitlines()
    ]
    segs = [x for x in lines if x["type"] == "segment"]
    assert [x["gate"] for x in segs] == [GATE_DISAGREE, GATE_AGREE]
    assert segs[1]["vosk"] == "добрий день" and segs[1]["vosk_conf"] == 1.0
    assert segs[1]["vosk_words"][0]["word"] == "добрий"
    assert segs[1]["whisper_words"][0]["word"] == "Добрий"
    assert segs[1]["reason"] == "sentence"
    (tr,) = [x for x in lines if x["type"] == "translation"]
    assert tr["mode"] == "two-text" and tr["text"] == "Добрий день."
    assert tr["hint"] == "добрий день" and tr["translation"] == "EN1."
    assert stats["session_record"]["on"] is True
    assert ts.get_stats()["session_record"]["on"] is False  # closed at stop
    assert any(e.get("status") == "session_record" and e["path"] for e in events)


def test_session_recording_is_off_by_default_and_toggles_live(tmp_path):
    ts, streams, llm, _ = _recording_system(tmp_path)
    assert ts.start()
    try:
        t = speak(streams, "Раз.", "раз")
        assert wait_for(lambda: len(llm.calls) == 1)
        assert not tmp_path.exists() or not list(tmp_path.iterdir())
        ts.apply_settings(dataclasses.replace(ts.settings, record_session=True))
        speak(streams, "Два.", "два", t)
        assert wait_for(lambda: len(llm.calls) == 2)
        assert ts.get_stats()["session_record"]["on"] is True
    finally:
        ts.stop()
    (session,) = list(tmp_path.iterdir())
    assert [p.name for p in session.glob("*.wav")] == ["seg-00002.wav"]


def test_session_recording_write_error_is_explicit(tmp_path):
    from src.core.session_record import SessionRecorder

    rec = SessionRecorder(tmp_path)
    path = rec.start()
    assert rec.active and path is not None
    path.chmod(0o500)  # read-only directory: the WAV write fails
    try:
        rec.segment(1, np.zeros(10, dtype=np.int16), 16000, {})
    finally:
        path.chmod(0o700)
    assert not rec.active and "PermissionError" in rec.error


def test_tts_is_warmed_up_at_start():
    from test_translation_system import FakeTts

    tts = FakeTts()
    ts, streams, llm, events = system(vosk=False, use=ServiceUse(vosk=OFF))
    ts._pools.tts.client = tts
    stats = run(ts, streams, [("Раз.", None)], lambda: tts.calls)
    assert tts.warmups == 1 and [c[0] for c in tts.calls] == ["EN1."]
    assert stats["warmup"]["tts"]["error"] is None
    assert any(e.get("status") == "warmup" and e["kind"] == "tts" for e in events)


def test_gate_and_record_events_are_described():
    from src.ui.controller.ui_controller import describe_pipeline_event

    level, msg = describe_pipeline_event(
        {
            "status": "vosk_gate",
            "reason": GATE_DISAGREE,
            "original_text": "Дякую!",
            "check_text": "так",
        }
    )
    assert level == "INFO" and "не збігся" in msg
    level, msg = describe_pipeline_event(
        {"status": "session_record", "path": None, "error": "PermissionError: x"}
    )
    assert level == "ERROR"


def test_latin_terms_do_not_count_against_a_cyrillic_vosk_model():
    # was: agree 0.00 -> gate disagree -> dropped
    assert agreement(S19, V19, "uk") is None
    assert gate_reason(S19, V19, "uk") == GATE_FOREIGN_TERMS
    # a Latin-script model hears Latin words: they count there
    assert gate_reason(S19, "dzień dobry wszystkim", "pl") == GATE_DISAGREE
    # ... and a pl model cannot write Cyrillic
    assert gate_reason("Привіт, я Ярослав.", "dzień dobry", "pl") == (
        GATE_FOREIGN_TERMS
    )
    # unknown language: no script rule
    assert gate_reason(S19, V19, None) == GATE_DISAGREE
    # mixed line (21:14:24): only the Cyrillic words are compared
    assert (
        gate_reason(
            "Привіт, я Ярослав, FullStack Ingenier.", "привід так інженер", "uk"
        )
        == GATE_AGREE
    )
    # a Cyrillic hallucination is still dropped next to Latin words
    assert gate_reason("Дякую за перегляд! Docker", "що можна зробити", "uk") == (
        GATE_DISAGREE
    )


# -- pipeline-start warm-up of every service (owner 2026-10-31) --------------


def test_start_opens_both_streams_and_warms_llm_tts_in_parallel():
    streams = FakeStreams(asr={"hold": True}, vosk={"hold": True})
    ts, streams, llm, events = system(streams=streams)
    capture = ts._capture_factory
    assert ts.start()
    try:
        # capture runs at once: audio goes to the opening streams' rings
        capture.push()
        assert wait_for(lambda: streams["asr"].fed and streams["vosk"].fed)
        assert streams["asr"].language == "uk"
        assert wait_for(
            lambda: set(ts.get_stats()["warmup_pending"]) == {"asr", "vosk"}
        )
        streams["vosk"].ready(loaded=True)
        streams["asr"].ready()
        assert wait_for(lambda: not ts.get_stats()["warmup_pending"])
        stats = ts.get_stats()
    finally:
        ts.stop()
    assert streams["asr"].stopped and streams["vosk"].stopped
    assert set(stats["warmup"]) == {"asr", "vosk", "llm", "tts"}
    assert stats["warmup"]["vosk"]["loaded"] and not stats["warmup"]["asr"]["loaded"]
    states = [e for e in events if e.get("status") == "warmup_state"]
    assert states[0]["ready"] is False
    assert sorted(states[0]["pending"]) == ["asr", "llm", "tts", "vosk"]
    # «прогрів…» names what is still warming after each service
    assert {"status": "warmup_state", "ready": False, "pending": ["asr"]} in states
    assert states[-1]["ready"] is True and states[-1]["failed"] == []


def test_failed_service_warm_up_is_reported_and_translation_goes_on():
    streams = FakeStreams(vosk={"error": "vosk: unreachable: no endpoint"})
    ts, streams, llm, events = system(streams=streams)
    stats = run(ts, streams, [("Добрий день.", None)], lambda: llm.calls)
    assert "no endpoint" in stats["warmup"]["vosk"]["error"]
    assert any(
        e.get("status") == "warmup_state" and e.get("failed") == ["vosk"]
        for e in events
    )
    assert llm.calls == ["Добрий день."]


def test_warm_up_events_are_described():
    from src.ui.controller.ui_controller import describe_pipeline_event

    assert describe_pipeline_event(
        {"status": "warmup_state", "ready": False, "pending": ["asr", "vosk"]}
    ) == ("INFO", "Прогрів… (ASR, Vosk)")
    assert describe_pipeline_event(
        {"status": "warmup_state", "ready": True, "ms": 812.0, "failed": []}
    ) == ("INFO", "Усе прогріто за 812.0 мс від старту")
    level, msg = describe_pipeline_event(
        {
            "status": "warmup",
            "kind": "asr",
            "warmup": {"url": "http://a", "error": None, "ms": 9.0, "loaded": True},
        }
    )
    assert level == "INFO" and msg.endswith("(модель завантажено)")
