"""Whisper + rt-phonetic cross-check in the streaming translate pipeline
(owner 2026-11-01): the per-unit letter gate, sound-mode ("condition F")
LLM messages, the whisper-only paths (off / unavailable / timeout / no model
of the language) visible and counted, the meta-reply retry, removed Vosk
config keys as explicit errors, rt-phonetic in discovery and the kill
switch, the session recording."""

import dataclasses
import json
import threading

import httpx
import numpy as np
import pytest

from src.clients import Directory, ServiceEntry, ServiceError, build_pools
from src.clients.discovery import endpoints
from src.clients.llm import LlmClient
from src.clients.phonetic import PhoneticClient, PhoneticResult, PhoneticWord
from src.clients.pool import EndpointPool
from src.core.crosscheck import (
    GATE_AGREE,
    GATE_DISAGREE,
    GATE_FOREIGN_TERMS,
    GATE_MIN_SIMILARITY,
    GATE_NO_WORD,
    GATE_SHORT_WORDS,
    Aligned,
    gate_reason,
    letters,
    similarity,
)
from src.core.prompt import (
    DEFAULT_SOUND_SYSTEM_PROMPT,
    build_messages,
    sound_template_errors,
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
from src.translation_system import TranslationSystem, phonetic_budget_s
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


class SoundLlm(SlowLlm):
    """Records (whisper text, SOUND text or None, NEXT); ``replies`` = what
    each call answers (default ``EN<n>.``)."""

    def __init__(self, replies=()):
        super().__init__(0)
        self.sounds = []
        self.nexts = []
        self.replies = list(replies)

    def translate_stream(self, text, source_lang, context=(), sound=None, next_text=""):
        self.calls.append(text)
        self.sounds.append(None if sound is None else sound.sound_text)
        self.nexts.append(next_text)
        reply = self.replies.pop(0) if self.replies else f"EN{len(self.calls)}."
        yield from reply.split("|")


class FakePhonetic(FakeModelService):
    """rt-phonetic: answers each unit with the next scripted reply - a
    transcript text (words spread over the sent audio) or a ServiceError.
    The start warm-up (its own thread) is counted apart."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []  # (samples, language, timeout_s)
        self.warmups = []

    def transcribe(self, audio, language, sample_rate=16000, *, timeout_s=None):
        if threading.current_thread().name == "rt-phonetic-warmup":
            self.warmups.append(language)
            return PhoneticResult("", language)
        self.calls.append((len(audio), language, timeout_s))
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        ws = reply.split()
        dur = len(audio) / sample_rate
        step = dur / max(1, len(ws))
        return PhoneticResult(
            reply,
            language,
            tuple(
                PhoneticWord(w, round(i * step, 3), round((i + 1) * step, 3))
                for i, w in enumerate(ws)
            ),
            dur,
        )


def settings(**kw):
    local = LocalUrls(llm="http://llm", tts="http://tts")
    kw.setdefault("source_language", "uk")
    return EdgeSettings(mode="translate", local=local, **kw)


def system(llm=None, streams=None, phonetic=(), **kw):
    """(ts, streams, llm, events); ``phonetic`` = replies of a configured
    rt-phonetic, None = none configured. ``ts.phonetic`` is the fake."""
    capture, events = FakeCapture(), []
    llm = llm or SoundLlm()
    streams = streams or FakeStreams()
    fake = None if phonetic is None else FakePhonetic(phonetic)
    ts = TranslationSystem(
        settings(**kw),
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: FakePools(FakeAsr(), SlowTts(0), fake),
        llm_target_factory=lambda s, d: StaticTarget("http://llm"),
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=lambda a, sr: None,
        stream_factory=streams,
    )
    ts.phonetic = fake
    ts.set_status_callback(events.append)
    return ts, streams, llm, events


def speak(streams, whisper, start=0.0, language="uk", eos=True):
    msg = final(whisper, start, eos=eos, language=language)
    streams["asr"].emit(msg)
    return msg["end"] + 0.5


def run(ts, streams, utterances, until, language="uk"):
    assert ts.start()
    try:
        t = 0.0
        for whisper in utterances:
            t = speak(streams, whisper, t, language)
        assert wait_for(until)
        return ts.get_stats()
    finally:
        ts.stop()


# -- the letter gate ----------------------------------------------------------


def test_letters_drop_case_spaces_punctuation_digits_apostrophes():
    assert letters("Пам'ять, 2 ГБ!") == "пам" + "ятьгб"


@pytest.mark.parametrize(
    "whisper, sound",
    [
        # the owner's live units 2026-11-01 and how a letter model spells them
        ("Перевірка висперстоянно тіювшою.", "перевірка віспер постійно дістаю"),
        ("Нова модель в бремі з виспі.", "нова модель вбудована з віспер"),
        ("А воск зараз відповідає дві.", "а воск зараз відповідає двічі"),
        ("Зараз ввозк займає приблизно 1 гигабайт.", "зараз воск займає приблизно"),
        ("Добре.", "добре"),
    ],
)
def test_gate_keeps_real_speech_spelled_by_sound(whisper, sound):
    assert similarity(whisper, sound, "uk") >= GATE_MIN_SIMILARITY
    assert gate_reason(whisper, sound, "uk") == GATE_AGREE


@pytest.mark.parametrize(
    "whisper, sound",
    [
        # whisper hallucinations against what was actually said
        ("Дякую за перегляд!", "можна зробити ще одну спробу"),
        ("Субтитри зроблені спільнотою.", "тести шов на синтетичному голосі"),
        ("Продовження буде.", "швидкість"),
    ],
)
def test_gate_drops_another_phrase(whisper, sound):
    assert similarity(whisper, sound, "uk") < GATE_MIN_SIMILARITY
    assert gate_reason(whisper, sound, "uk") == GATE_DISAGREE


def test_empty_transcript_is_heard_silence():
    assert gate_reason("Дякую.", "", "uk") == GATE_NO_WORD
    assert gate_reason("Дякую.", "  ", "uk") == GATE_NO_WORD


def test_short_whisper_has_no_ratio():
    assert similarity("Це я.", "ой", "uk") is None
    assert gate_reason("Це я.", "ой", "uk") == GATE_SHORT_WORDS


S19 = (
    "CPress, Stack, TypeScript, React, SGS, NodeJS, Python, PostgreSQL, Redis,"
    " Docker..."
)


def test_latin_terms_are_no_evidence_against_a_cyrillic_model():
    sound = "сі прес стек тайпскрипт реакт ноуд джейес пайтон докер"
    assert gate_reason(S19, sound, "uk") == GATE_FOREIGN_TERMS
    # mixed: only the Cyrillic words are compared
    assert (
        gate_reason("Ми пишемо на TypeScript і Docker", "ми пишемо на тайпскрипт", "uk")
        == GATE_AGREE
    )
    # a Cyrillic hallucination next to Latin words is still dropped
    assert gate_reason("Дякую за перегляд! Docker", "що можна зробити", "uk") == (
        GATE_DISAGREE
    )
    # unknown language: no script rule
    assert gate_reason(S19, sound, None) == GATE_DISAGREE


def test_aligned_cuts_phonetic_words_by_time():
    a = Aligned.from_words(
        [Word("Раз", 0.0, 0.5), Word("два.", 0.5, 1.0), Word("Три", 1.0, 2.0)],
        words("раз два три", 0.0, 0.66),
    )
    head, tail = a.cut(2)
    assert head.sound_text == "раз два" and tail.sound_text == "три"
    assert (head + tail).sound_text == "раз два три"


# -- prompt / settings ----------------------------------------------------------


def test_sound_messages_put_sound_first_and_name_it_primary():
    s = LlmSettings()
    msgs = build_messages(
        s.sound_system_prompt,
        s.sound_user_template,
        "Дякую.",
        "uk",
        sound="дякую",
    )
    system_text = msgs[0]["content"]
    assert system_text.startswith("You are a live Ukrainian-to-English interpreter.")
    assert "trust SOUND" in system_text and "«віспер»" in system_text
    assert "{{" not in system_text
    assert msgs[-1] == {"role": "user", "content": "SOUND: дякую\nTEXT: Дякую."}


def test_sound_template_validation_and_removed_vosk_placeholder():
    assert sound_template_errors("t", "S: {{sound_text}} T: {{text}}", True) == []
    assert sound_template_errors("t", "{{text}}", True)  # no {{sound_text}}
    assert sound_template_errors("s", DEFAULT_SOUND_SYSTEM_PROMPT, False) == []
    errors = sound_template_errors("t", "{{text}} {{vosk_text}}", True)
    assert any("{{vosk_text}} is removed; use {{sound_text}}" in e for e in errors)


def test_config_parses_phonetic_use_and_sound_prompts():
    s = resolve_edge_settings(
        {
            "services": {
                "use": {"phonetic": "off"},
                "local": {"phonetic": "http://127.0.0.1:8092/"},
                "llm": {"sound_user_template": "SOUND: {{sound_text}}\n{{text}}"},
            }
        },
        {},
    )
    assert s.use.phonetic == OFF and s.local.phonetic == "http://127.0.0.1:8092"
    assert s.llm.sound_user_template == "SOUND: {{sound_text}}\n{{text}}"
    assert s.validate() == []
    asr_off = dataclasses.replace(s, use=ServiceUse(asr=OFF))
    assert "use.asr: asr cannot be switched off" in asr_off.validate()


@pytest.mark.parametrize(
    "cfg, old, new",
    [
        (
            {"services": {"use": {"vosk": "cyborg"}}},
            "services.use.vosk",
            "use.phonetic",
        ),
        ({"services": {"use": {"vosk": None}}}, "services.use.vosk", "use.phonetic"),
        (
            {"services": {"local": {"vosk": "http://127.0.0.1:8092"}}},
            "services.local.vosk",
            "local.phonetic",
        ),
        (
            {"services": {"llm": {"check_user_template": "{{vosk_text}}"}}},
            "services.llm.check_user_template",
            "llm.sound_user_template",
        ),
        (
            {"services": {"llm": {"check_system_prompt": ""}}},
            "services.llm.check_system_prompt",
            "llm.sound_system_prompt",
        ),
        (
            {"ui": {"show_vosk_text": True}},
            "ui.show_vosk_text",
            "ui.show_phonetic_text",
        ),
    ],
)
def test_removed_vosk_keys_are_explicit_errors_naming_the_new_key(cfg, old, new):
    errors = resolve_edge_settings(cfg, {}).validate()
    assert any(old in e and new in e for e in errors), errors


def test_vosk_in_allowed_services_names_phonetic():
    s = EdgeSettings(allowed_services=("vosk:cyborg",))
    assert any("phonetic:cyborg" in e for e in s.validate())


def test_llm_client_sends_sound_prompts_per_chunk_with_next():
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
    sound = Aligned.from_words(
        [Word("Раз", 0, 1), Word("два", 1, 2), Word("три", 2, 3)],
        words("раз два три", 0.0, 1.0),
    )
    out = client.translate(
        "Раз два три", "uk", [("x", "X")], sound=sound, next_text="чотири"
    )
    assert out == "T1. T2."
    first, second = (b["messages"] for b in bodies)
    assert first[0]["content"].startswith("You are a live Ukrainian")
    # whisper-only context pairs are not shown in sound mode
    assert first[1:] == [
        {
            "role": "user",
            "content": "SOUND: раз два\nTEXT: Раз два\nNEXT (do not translate):"
            " три чотири",
        }
    ]
    assert second[1:] == [
        {"role": "user", "content": "SOUND: раз два\nTEXT: Раз два"},
        {"role": "assistant", "content": "T1."},
        {
            "role": "user",
            "content": "SOUND: три\nTEXT: три\nNEXT (do not translate): чотири",
        },
    ]


def test_phonetic_client_request_and_words():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(
            200,
            json={
                "text": "привіт",
                "language": "uk",
                "words": [{"word": "привіт", "start": 0.1, "end": 0.5}],
                "duration": 0.6,
            },
        )

    c = PhoneticClient("http://p:8092", transport=httpx.MockTransport(handler))
    r = c.transcribe(np.ones(1600, dtype=np.int16), language="uk")
    assert seen["url"] == "http://p:8092/v1/audio/transcriptions"
    assert b'name="language"' in seen["body"] and b"uk" in seen["body"]
    assert r == PhoneticResult("привіт", "uk", (PhoneticWord("привіт", 0.1, 0.5),), 0.6)


def test_phonetic_client_409_is_wrong_language():
    def handler(req):
        return httpx.Response(
            409,
            json={"error": {"code": "model_language_mismatch", "message": "uk"}},
        )

    c = PhoneticClient("http://p:8092", transport=httpx.MockTransport(handler))
    with pytest.raises(ServiceError) as e:
        c.transcribe(np.ones(1600, dtype=np.int16), language="pl")
    assert e.value.kind == "wrong_language"


# -- pipeline -------------------------------------------------------------------


def test_unit_audio_goes_to_phonetic_and_sound_is_primary():
    ts, streams, llm, _ = system(phonetic=["добрий день"])
    stats = run(ts, streams, ["Добрий день."], lambda: llm.calls)
    # final(): 2 words x 0.3 s -> the unit's 0.6 s of audio, with the budget
    assert ts.phonetic.calls == [(9600, "uk", 1.5)]  # the budget floor
    assert ts.phonetic.warmups == ["uk"]
    assert llm.sounds == ["добрий день"]
    assert stats["phonetic_checked"] == 1
    assert stats["phonetic"] == {"mode": "on", "error": None}
    assert stats["warmup"]["phonetic"]["error"] is None


def test_hallucination_in_silence_is_dropped_and_visible():
    ts, streams, llm, events = system(phonetic=["", "добрий день"])
    stats = run(ts, streams, ["Дякую за перегляд!", "Добрий день."], lambda: llm.calls)
    assert llm.calls == ["Добрий день."]
    assert stats["gate_dropped"] == 1 and stats["gate_disagree"] == 0
    assert {
        "status": "phonetic_gate",
        "reason": GATE_NO_WORD,
        "original_text": "Дякую за перегляд!",
        "sound_text": "",
    } in events


def test_another_phrase_is_dropped_as_disagree():
    ts, streams, llm, _ = system(phonetic=["тести на синтетичному голосі", "раз"])
    stats = run(
        ts, streams, ["Субтитри зроблені спільнотою.", "Раз."], lambda: llm.calls
    )
    assert llm.calls == ["Раз."]
    assert stats["gate_dropped"] == 1 and stats["gate_disagree"] == 1


def test_foreign_terms_pass_and_are_counted():
    ts, streams, llm, _ = system(phonetic=["тайпскрипт докер"])
    stats = run(ts, streams, ["TypeScript, Docker."], lambda: llm.calls)
    assert llm.sounds == ["тайпскрипт докер"]
    assert stats["gate_foreign_terms"] == 1


@pytest.mark.parametrize(
    "error, counter, mode",
    [
        (ServiceError("phonetic", "timeout", "read"), "phonetic_timeout", "on"),
        (
            ServiceError("phonetic", "unreachable", "refused"),
            "phonetic_unavailable",
            "unavailable",
        ),
    ],
)
def test_phonetic_failure_is_whisper_only_counted_and_visible(error, counter, mode):
    ts, streams, llm, events = system(phonetic=[error, "два"])
    stats = run(ts, streams, ["Раз.", "Два."], lambda: len(llm.calls) == 2)
    assert llm.sounds == [None, "два"]  # the next unit has it back
    assert stats[counter] == 1 and stats["whisper_only"] == 1
    assert stats["phonetic"]["mode"] == "on"
    assert any(
        e.get("status") == "phonetic_whisper_only" and e["original_text"] == "Раз."
        for e in events
    )
    if mode == "unavailable":
        states = [
            e["phonetic"]["mode"] for e in events if e.get("status") == "phonetic_state"
        ]
        assert states == ["unavailable", "on"]


@pytest.mark.parametrize(
    "audio_s, budget",
    [
        (0.6, 1.5),  # floor: a short unit keeps the old 1.5 s
        (4.0, 1.5),
        (6.0, 2.0),
        # live 2026-11-01 §18 unit #26: 10.2 s timed out on the fixed 1.5 s
        (10.18, 3.045),
        (14.0, 4.0),
        (30.0, 4.0),  # capped
    ],
)
def test_phonetic_budget_grows_with_the_unit_audio(audio_s, budget):
    assert phonetic_budget_s(audio_s) == pytest.approx(budget)


def test_phonetic_request_gets_the_budget_of_its_unit_audio():
    long = " ".join(["слово"] * 19) + "."  # 19 words * 0.3 s
    ts, streams, llm, _ = system(phonetic=["раз", "слово " * 19])
    run(ts, streams, ["Раз.", long], lambda: len(llm.calls) == 2)
    (short_s, _, short_budget), (long_s, _, long_budget) = ts.phonetic.calls
    assert short_budget == 1.5
    assert long_budget == pytest.approx(phonetic_budget_s(long_s / 16000))
    assert long_budget > 1.9


def test_language_without_a_model_is_whisper_only_and_never_asked_again():
    mismatch = ServiceError("phonetic", "wrong_language", "uk model", 409)
    ts, streams, llm, events = system(phonetic=[mismatch], source_language="auto")
    stats = run(
        ts,
        streams,
        ["Dzień dobry.", "Jak się masz?"],
        lambda: len(llm.calls) == 2,
        language="pl",
    )
    assert llm.sounds == [None, None]
    assert len(ts.phonetic.calls) == 1  # pl is not asked again
    assert stats["phonetic"] == {
        "mode": "no_language",
        "error": "фонетика недоступна для польської мови",
    }
    assert stats["whisper_only"] == 2


def test_set_language_without_a_model_shows_at_warm_up():
    class NoPl(FakePhonetic):
        def transcribe(self, audio, language, sample_rate=16000, *, timeout_s=None):
            raise ServiceError("phonetic", "wrong_language", "uk model", 409)

    ts, streams, llm, _ = system(source_language="pl")
    ts._pools.phonetic.client = NoPl()
    stats = run(ts, streams, ["Dzień dobry."], lambda: llm.calls, language="pl")
    assert llm.sounds == [None]
    assert stats["phonetic"]["mode"] == "no_language"
    assert "польської" in stats["phonetic"]["error"]


def test_phonetic_off_is_whisper_only_without_a_call():
    ts, streams, llm, _ = system(phonetic=None, use=ServiceUse(phonetic=OFF))
    stats = run(ts, streams, ["Раз."], lambda: llm.calls)
    assert llm.sounds == [None]
    assert stats["phonetic"]["mode"] == "off" and stats["whisper_only"] == 0
    assert "phonetic" not in stats["warmup"]


def test_phonetic_not_found_is_visible():
    ts, streams, llm, _ = system(phonetic=None)
    stats = run(ts, streams, ["Раз."], lambda: llm.calls)
    assert stats["phonetic"]["mode"] == "unavailable"
    assert "rt-phonetic не знайдено" in stats["phonetic"]["error"]
    assert stats["whisper_only"] == 1


def test_meta_reply_is_retried_whisper_only_once():
    llm = SoundLlm(replies=["I notice you provided two transcripts.", "Hi there."])
    ts, streams, llm, events = system(llm=llm, phonetic=["привіт"])
    stats = run(ts, streams, ["Привіт."], lambda: ts._pools.tts.client.calls)
    assert llm.sounds == ["привіт", None]
    assert stats["retry_whisper_only"] == 1 and stats["llm_rejected"] == 0
    assert ts._pools.tts.client.calls == ["Hi there."]
    assert any(
        e.get("status") == "translation_retry" and e["reason"] == "meta" for e in events
    )
    assert ts._translation_context[-1] == ("Привіт.", "Hi there.")


def test_rejected_after_tts_got_sentences_is_not_retried():
    long = "Раз два три чотири п'ять."
    llm = SoundLlm(replies=["One two three.|Please provide the transcript."])
    ts, streams, llm, _ = system(llm=llm, phonetic=["раз два три чотири пять"])
    stats = run(ts, streams, [long], lambda: ts.get_stats()["llm_rejected"])
    assert len(llm.calls) == 1
    assert stats["rejected_after_tts"] == 1 and stats["retry_whisper_only"] == 0


def test_recognized_unit_carries_the_sound_for_the_ui():
    ts, streams, llm, _ = system(phonetic=["добрий день"])
    assert ts.start()
    try:
        speak(streams, "Добрий день.")
        assert wait_for(lambda: llm.calls)
        pending = ts.get_stats()["pending_recognized"]
    finally:
        ts.stop()
    assert pending == [{"text": "Добрий день.", "sound": "добрий день"}]


def test_phonetic_events_are_described_for_the_log():
    from src.ui.controller.ui_controller import describe_pipeline_event

    level, msg = describe_pipeline_event(
        {
            "status": "phonetic_gate",
            "reason": GATE_DISAGREE,
            "original_text": "Дякую!",
            "sound_text": "так",
        }
    )
    assert level == "INFO" and "не збігся зі звуком" in msg
    level, msg = describe_pipeline_event(
        {
            "status": "phonetic_state",
            "phonetic": {
                "mode": "no_language",
                "error": "фонетика недоступна для польської мови",
            },
        }
    )
    assert level == "WARN" and msg.startswith("фонетика недоступна для польської")
    level, msg = describe_pipeline_event(
        {
            "status": "phonetic_whisper_only",
            "reason": "timeout",
            "error": "x",
            "original_text": "Раз.",
        }
    )
    assert level == "WARN" and "не встигла" in msg
    level, msg = describe_pipeline_event(
        {"status": "translation_retry", "reason": "meta", "original_text": "Раз."}
    )
    assert level == "WARN" and "лише з whisper" in msg


# -- session recording ------------------------------------------------------------


def _recording_system(tmp_path, **kw):
    from src.core.session_record import SessionRecorder

    ts, streams, llm, events = system(**kw)
    ts._recorder_factory = lambda: SessionRecorder(tmp_path)
    return ts, streams, llm, events


def test_session_recording_writes_wav_and_jsonl(tmp_path):
    ts, streams, llm, events = _recording_system(
        tmp_path, record_session=True, phonetic=["що можна зробити", "добрий день"]
    )
    stats = run(ts, streams, ["Дякую за перегляд!", "Добрий день."], lambda: llm.calls)
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
    assert segs[1]["phonetic"] == "добрий день" and segs[1]["similarity"] > 0.9
    assert segs[1]["mode"] == "sound"
    # phonetic word times on the stream timeline (unit starts at 2.6 s)
    assert segs[1]["phonetic_words"][0]["word"] == "добрий"
    assert segs[1]["phonetic_words"][0]["start"] == pytest.approx(segs[1]["start_s"])
    assert segs[1]["whisper_words"][0]["word"] == "Добрий"
    (tr,) = [x for x in lines if x["type"] == "translation"]
    assert tr["mode"] == "sound" and tr["text"] == "Добрий день."
    assert tr["sound"] == "добрий день" and tr["translation"] == "EN1."
    assert stats["session_record"]["on"] is True
    assert ts.get_stats()["session_record"]["on"] is False  # closed at stop


def test_session_recording_marks_whisper_only_and_retry(tmp_path):
    llm = SoundLlm(replies=["The SOUND says hi.", "Hi."])
    ts, streams, llm, _ = _recording_system(
        tmp_path, record_session=True, llm=llm, phonetic=["привіт"]
    )
    run(ts, streams, ["Привіт."], lambda: len(llm.calls) == 2)
    (session,) = list(tmp_path.iterdir())
    lines = [
        json.loads(x) for x in (session / "phrases.jsonl").read_text().splitlines()
    ]
    trs = [x for x in lines if x["type"] == "translation"]
    assert [(t["mode"], t["rejected"]) for t in trs] == [
        ("sound", "meta"),
        ("retry", None),
    ]
    assert trs[1]["sound"] is None


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


# -- discovery / pools / kill switch -------------------------------------------------

CLUSTER = [
    ServiceEntry("phonetic", "me", "http://100.1.1.1:8092", "tv"),
    ServiceEntry("phonetic", "other", "http://other:8092", "to"),
]


@pytest.mark.parametrize(
    "use, urls",
    [
        (None, ["http://127.0.0.1:8092", "http://other:8092"]),
        ("other", ["http://other:8092"]),
        (OFF, []),
    ],
)
def test_phonetic_endpoints_follow_use(use, urls):
    s = EdgeSettings(local=LocalUrls(node="me"), use=ServiceUse(phonetic=use))
    d = Directory(list(CLUSTER), "cache")
    assert [e.url for e in endpoints("phonetic", s, d)] == urls
    pools = build_pools(s, d)
    try:
        assert (pools.phonetic is None) == (not urls)
        assert ("phonetic" in pools.by_name()) == bool(urls)
    finally:
        pools.close()


@pytest.mark.parametrize("use, has", [(None, True), (OFF, False)])
def test_kill_switch_clients_include_local_phonetic(use, has):
    s = EdgeSettings(
        mode="transcribe", local=LocalUrls(node="me"), use=ServiceUse(phonetic=use)
    )
    ts = TranslationSystem(
        s,
        directory_factory=lambda _s: Directory(list(CLUSTER), "cache"),
        llm_target_factory=lambda _s, _d: None,
    )
    clients = ts.local_model_clients()
    try:
        assert ("phonetic" in clients) == has
        if has:
            assert clients["phonetic"].base_url == "http://127.0.0.1:8092"
    finally:
        for c in clients.values():
            c.close()
        ts.cleanup()


def test_pool_call_once_never_fails_over_within_the_call():
    from src.clients.base import Credential
    from src.clients.discovery import Endpoint

    eps = [Endpoint("phonetic", n, f"http://{n}", Credential()) for n in ("a", "b")]
    pool = EndpointPool("phonetic", eps, lambda e: e.url)
    seen = []

    def fn(url):
        seen.append(url)
        raise ServiceError("phonetic", "timeout", "slow")

    with pytest.raises(ServiceError):
        pool.call_once(fn)
    assert seen == ["http://a"]
    with pytest.raises(ServiceError):
        pool.call_once(fn)  # the next unit goes to the next endpoint
    assert seen == ["http://a", "http://b"]
