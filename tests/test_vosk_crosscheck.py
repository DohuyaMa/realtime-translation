"""TASK-SYS-12: whisper + Vosk cross-check in the translate pipeline -
parallel calls, the gate, two-text LLM messages, the hold/merge of both
texts, Vosk off/unavailable visible, rt-vosk in the kill switch."""

import dataclasses
import json
import time

import httpx
import numpy as np
import pytest

from src.clients import Directory, ServiceEntry, ServiceError, build_pools
from src.clients.asr import AsrResult
from src.clients.discovery import endpoints
from src.clients.llm import LlmClient
from src.clients.vosk import VoskClient, VoskResult, VoskWord
from src.core.crosscheck import (
    GATE_DISAGREE,
    GATE_NO_WORD,
    Aligned,
    agreement,
    gate_reason,
    has_real_word,
    mean_conf,
    whisper_word_times,
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
from src.segmentation import Segment
from src.translation_system import TranslationSystem
from test_pipeline_stages import SlowAsr, SlowLlm, SlowTts
from test_translation_system import (
    FakeCapture,
    FakePools,
    FakeVad,
    StaticTarget,
    wait_for,
)


def seg(start=0.0, end=1.0, reason="end"):
    audio = np.ones(int((end - start) * 16000) or 1, dtype=np.int16)
    return Segment(audio=audio, start_s=start, end_s=end, reason=reason)


def words(text, start=0.0, step=0.2):
    return tuple(
        VoskWord(w, 1.0, start + i * step, start + (i + 1) * step)
        for i, w in enumerate(text.split())
    )


class FakeVosk:
    def __init__(self, results, delay=0.0):
        self.results = list(results)
        self.delay = delay
        self.calls = []

    def transcribe(self, audio, language, sample_rate=16000, **kw):
        self.calls.append({"language": language, "t": time.monotonic()})
        time.sleep(self.delay)
        r = self.results.pop(0) if self.results else VoskResult("", language)
        if isinstance(r, Exception):
            raise r
        return r


class CheckLlm(SlowLlm):
    """Records (whisper text, Vosk check text or None)."""

    def __init__(self):
        super().__init__(0)
        self.checks = []

    def translate_stream(self, text, source_lang, context=(), check=None):
        self.calls.append(text)
        self.checks.append(None if check is None else check.check_text)
        yield f"EN{len(self.calls)}."


def settings(**kw):
    local = LocalUrls(llm="http://llm", tts="http://tts")
    kw.setdefault("source_language", "uk")
    return EdgeSettings(mode="translate", local=local, **kw)


def system(asr, vosk, llm=None, **kw):
    vads, capture, events = [], FakeCapture(), []
    llm = llm or CheckLlm()

    def vad_factory(cfg):
        v = FakeVad(cfg)
        vads.append(v)
        return v

    ts = TranslationSystem(
        settings(**kw),
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: FakePools(asr, SlowTts(0), vosk),
        llm_target_factory=lambda s, d: StaticTarget("http://llm"),
        vad_stream_factory=vad_factory,
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=lambda a, sr: None,
    )
    ts.set_status_callback(events.append)
    return ts, vads, capture, llm, events


def run(ts, vads, capture, segments, until):
    assert ts.start()
    try:
        for s in segments:
            vads[-1].pending.append(s)
            capture.push()
            time.sleep(0.02)
        assert wait_for(until)
        return ts.get_stats()
    finally:
        ts.stop()


# -- pure helpers ---------------------------------------------------------


def test_gate_needs_a_word_of_two_letters():
    assert not has_real_word("")
    assert not has_real_word("е і а")
    assert has_real_word("е до")


def test_whisper_word_times_follow_segments():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "Раз два."},
        {"start": 2.0, "end": 4.0, "text": "три"},
    ]
    assert whisper_word_times("Раз два. три", segs, 4.0, 10.0) == (
        10.25,
        10.75,
        13.0,
    )
    # text not covered word for word: spread over the whole audio
    assert whisper_word_times("a b", [], 2.0, 0.0) == (0.5, 1.5)


def test_aligned_cut_splits_vosk_words_by_time():
    a = Aligned.build(
        "Раз два. Три",
        [
            {"start": 0.0, "end": 1.0, "text": "Раз два."},
            {"start": 1.0, "end": 2.0, "text": "Три"},
        ],
        words("раз два три", 0.0, 0.66),
        "раз два три",
        2.0,
        0.0,
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


def test_llm_client_sends_check_prompts_per_chunk():
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
    check = Aligned.build(
        "Раз два три",
        [],
        words("раз два три", 0.0, 1.0),
        "раз два три",
        3.0,
        0.0,
    )
    out = client.translate("Раз два три", "uk", [("x", "X")], check=check)
    assert out == "T1. T2."
    first, second = (b["messages"] for b in bodies)
    assert first[0]["content"].startswith("You are a live Ukrainian")
    # whisper-only context pairs are not shown in two-text mode
    assert first[1:] == [{"role": "user", "content": "TEXT: Раз два\nHINT: раз два"}]
    assert second[1:] == [
        {"role": "user", "content": "TEXT: Раз два\nHINT: раз два"},
        {"role": "assistant", "content": "T1."},
        {"role": "user", "content": "TEXT: три\nHINT: три"},
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


# -- pipeline ---------------------------------------------------------------


def test_whisper_and_vosk_run_in_parallel():
    asr = SlowAsr([AsrResult("Добрий день.", "uk")], 0.3)
    vosk = FakeVosk([VoskResult("добрий день", "uk", words("добрий день"))], 0.3)
    ts, vads, capture, llm, _ = system(asr, vosk)
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert vosk.calls[0]["language"] == "uk"
    assert stats["timing"]["asr_ms"] < 500  # max of the two, not the sum
    assert llm.checks == ["добрий день"]
    assert stats["vosk_checked"] == 1 and stats["vosk"] == {"mode": "on", "error": None}


def test_gate_drops_segment_before_the_llm():
    asr = SlowAsr(
        [AsrResult("Дякую за перегляд!", "uk"), AsrResult("Добрий день.", "uk")], 0
    )
    vosk = FakeVosk([VoskResult("е", "uk"), VoskResult("добрий день", "uk")])
    ts, vads, capture, llm, events = system(asr, vosk)
    stats = run(ts, vads, capture, [seg(), seg()], lambda: llm.calls)
    assert llm.calls == ["Добрий день."]
    assert stats["vosk_gate_dropped"] == 1
    assert any(
        e.get("status") == "vosk_gate" and e["original_text"] == "Дякую за перегляд!"
        for e in events
    )


def test_held_tail_merges_both_texts():
    asr = SlowAsr(
        [AsrResult("Чикає ДОКИ", "uk"), AsrResult("До паузи, по факту.", "uk")], 0
    )
    vosk = FakeVosk(
        [
            VoskResult("чикає доки", "uk", words("чикає доки")),
            VoskResult("до паузи по факту", "uk", words("до паузи по факту")),
        ]
    )
    ts, vads, capture, llm, _ = system(asr, vosk)
    stats = run(ts, vads, capture, [seg(0, 1), seg(1.5, 3)], lambda: llm.calls)
    assert llm.calls == ["Чикає ДОКИ До паузи, по факту."]
    assert llm.checks == ["чикає доки до паузи по факту"]
    assert stats["asr_merged"] == 1


def test_complete_sentence_goes_with_its_vosk_words_and_tail_waits():
    whisper = AsrResult(
        "Не витрачайте час. Картки без",
        "uk",
        [
            {"start": 0.0, "end": 1.0, "text": "Не витрачайте час."},
            {"start": 1.0, "end": 2.0, "text": "Картки без"},
        ],
    )
    vosk_words = (
        VoskWord("не", 1, 0.0, 0.2),
        VoskWord("витрачайте", 1, 0.2, 0.6),
        VoskWord("час", 1, 0.6, 0.9),
        VoskWord("картки", 1, 1.1, 1.5),
        VoskWord("без", 1, 1.5, 1.9),
    )
    asr = SlowAsr([whisper, AsrResult("фізичної карти.", "uk")], 0)
    vosk = FakeVosk(
        [
            VoskResult("не витрачайте час картки без", "uk", vosk_words),
            VoskResult("фізичної карти", "uk", words("фізичної карти")),
        ]
    )
    ts, vads, capture, llm, _ = system(asr, vosk)
    run(ts, vads, capture, [seg(0, 2), seg(2.5, 4)], lambda: len(llm.calls) == 2)
    assert llm.calls == ["Не витрачайте час.", "Картки без фізичної карти."]
    assert llm.checks == ["не витрачайте час", "картки без фізичної карти"]


def test_vosk_off_is_whisper_only_and_visible():
    asr = SlowAsr([AsrResult("Добрий день.", "uk")], 0)
    ts, vads, capture, llm, _ = system(asr, None, use=ServiceUse(vosk=OFF))
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert llm.checks == [None]
    assert stats["vosk"] == {"mode": "off", "error": "вимкнено в налаштуваннях"}
    assert stats["vosk_whisper_only"] == 0  # a configured mode, not a problem


def test_vosk_not_found_is_visible():
    asr = SlowAsr([AsrResult("Добрий день.", "uk")], 0)
    ts, vads, capture, llm, _ = system(asr, None)
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert llm.checks == [None]
    assert stats["vosk"]["mode"] == "unavailable"
    assert stats["vosk_whisper_only"] == 1


def test_vosk_error_goes_whisper_only_then_back_on():
    asr = SlowAsr([AsrResult("Раз.", "uk"), AsrResult("Два.", "uk")], 0)
    vosk = FakeVosk(
        [ServiceError("vosk", "unreachable", "down"), VoskResult("два", "uk")]
    )
    ts, vads, capture, llm, events = system(asr, vosk)
    stats = run(ts, vads, capture, [seg(), seg()], lambda: len(llm.calls) == 2)
    assert llm.checks == [None, "два"]
    assert stats["vosk_whisper_only"] == 1 and stats["vosk"]["mode"] == "on"
    states = [e["vosk"]["mode"] for e in events if e.get("status") == "vosk_state"]
    assert states == ["unavailable", "on"]


def test_auto_language_first_phrase_waits_for_whisper_then_parallel():
    asr = SlowAsr([AsrResult("Dzień dobry.", "pl"), AsrResult("Cześć.", "pl")], 0)
    vosk = FakeVosk([VoskResult("dzień dobry", "pl"), VoskResult("cześć", "pl")])
    ts, vads, capture, llm, _ = system(asr, vosk, source_language="auto")
    stats = run(ts, vads, capture, [seg(), seg()], lambda: len(llm.calls) == 2)
    assert [c["language"] for c in vosk.calls] == ["pl", "pl"]
    assert stats["vosk_after_whisper"] == 1  # only the first phrase
    assert llm.checks == ["dzień dobry", "cześć"]


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
    asr = SlowAsr([AsrResult("Dzień dobry.", "pl")], 0)
    vosk = FakeVosk([err])
    ts, vads, capture, llm, _ = system(asr, vosk, source_language="pl")
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert llm.checks == [None]
    assert stats["vosk"]["mode"] == "unavailable"
    assert stats["vosk"]["error"].startswith("немає моделі для «pl»")
    assert stats["pending_recognized"] == [{"text": "Dzień dobry.", "check": None}]


def test_recognized_phrase_carries_vosk_text():
    asr = SlowAsr([AsrResult("Добрий день.", "uk")], 0)
    vosk = FakeVosk([VoskResult("добрий день", "uk", words("добрий день"))])
    ts, vads, capture, llm, _ = system(asr, vosk)
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert stats["pending_recognized"] == [
        {"text": "Добрий день.", "check": "добрий день"}
    ]


# -- segment-level gate: veto hallucinations, never delete real words (§10) --

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
    ("Дякую!", "так"),
]


@pytest.mark.parametrize("whisper,vosk", OWNER_LINES)
def test_gate_keeps_real_speech_with_garbled_vosk(whisper, vosk):
    assert gate_reason(whisper, vosk) is None
    assert agreement(whisper, vosk) > 0


@pytest.mark.parametrize("whisper,vosk", HALLUCINATIONS)
def test_gate_drops_whisper_phrase_vosk_disagrees_with(whisper, vosk):
    assert gate_reason(whisper, vosk) == GATE_DISAGREE
    assert agreement(whisper, vosk) == 0.0


def test_gate_silence_and_short_words():
    assert gate_reason("Дякую за перегляд!", "е") == GATE_NO_WORD
    # whisper without a word of >= 3 letters: no evidence, kept
    assert agreement("Ну, ок.", "та") is None
    assert gate_reason("Ну, ок.", "та") is None
    # split Vosk words still agree («інформа ції»)
    assert agreement("Інформації.", "інформа ції") == 1.0


def test_mean_conf():
    assert mean_conf(()) is None
    assert mean_conf((VoskWord("а", 0.5, 0, 1), VoskWord("б", 1.0, 1, 2))) == 0.75


def test_gate_drops_disagreeing_segment_and_counts_it():
    asr = SlowAsr(
        [AsrResult("Дякую за перегляд!", "uk"), AsrResult("Добрий день.", "uk")], 0
    )
    vosk = FakeVosk(
        [
            VoskResult("що можна зробити", "uk", words("що можна зробити")),
            VoskResult("добрий день", "uk", words("добрий день")),
        ]
    )
    ts, vads, capture, llm, events = system(asr, vosk)
    stats = run(ts, vads, capture, [seg(), seg()], lambda: llm.calls)
    assert llm.calls == ["Добрий день."]
    assert stats["vosk_gate_dropped"] == 1 and stats["vosk_gate_disagree"] == 1
    assert any(
        e.get("status") == "vosk_gate" and e["reason"] == GATE_DISAGREE for e in events
    )


def test_sparse_vosk_keeps_the_whole_whisper_text():
    text = "Запитайте про відео або аудіо, яке ви відтворювали."
    asr = SlowAsr([AsrResult(text, "uk")], 0)
    vosk = FakeVosk([VoskResult("або", "uk", words("або"))])
    ts, vads, capture, llm, _ = system(asr, vosk)
    stats = run(ts, vads, capture, [seg()], lambda: llm.calls)
    assert llm.calls == [text] and llm.checks == ["або"]
    assert stats["vosk_gate_dropped"] == 0


def test_held_piece_with_empty_vosk_span_is_still_translated():
    """Vosk words are split by interpolated time; a held tail whose span got
    no Vosk word was spoken all the same - no per-piece gate."""
    whisper = AsrResult(
        "Новий спосіб перегляду вкладок. Вертикальні вкладки",
        "uk",
        [
            {"start": 0.0, "end": 1.0, "text": "Новий спосіб перегляду вкладок."},
            {"start": 1.0, "end": 2.0, "text": "Вертикальні вкладки"},
        ],
    )
    vosk_words = words("новий спосіб перегляду вкладок", 0.0, 0.2)
    asr = SlowAsr([whisper], 0)
    vosk = FakeVosk([VoskResult("новий спосіб перегляду вкладок", "uk", vosk_words)])
    ts, vads, capture, llm, _ = system(asr, vosk)
    stats = run(ts, vads, capture, [seg(0, 2)], lambda: len(llm.calls) == 2)
    assert llm.calls == ["Новий спосіб перегляду вкладок.", "Вертикальні вкладки"]
    assert llm.checks == ["новий спосіб перегляду вкладок", ""]
    assert stats["vosk_gate_dropped"] == 0 and stats["asr_held_flushed"] == 1


# -- session recording and per-phrase log ------------------------------------


def _recording_system(tmp_path, asr, vosk, **kw):
    from src.core.session_record import SessionRecorder

    ts, vads, capture, llm, events = system(asr, vosk, **kw)
    ts._recorder_factory = lambda: SessionRecorder(tmp_path)
    return ts, vads, capture, llm, events


def test_session_recording_writes_wav_and_jsonl(tmp_path):
    asr = SlowAsr(
        [AsrResult("Дякую за перегляд!", "uk"), AsrResult("Добрий день.", "uk")], 0
    )
    vosk = FakeVosk(
        [
            VoskResult("що можна", "uk", words("що можна")),
            VoskResult("добрий день", "uk", words("добрий день")),
        ]
    )
    ts, vads, capture, llm, events = _recording_system(
        tmp_path, asr, vosk, record_session=True
    )
    stats = run(ts, vads, capture, [seg(), seg()], lambda: llm.calls)
    (session,) = list(tmp_path.iterdir())
    assert sorted(p.name for p in session.glob("*.wav")) == [
        "seg-00001.wav",
        "seg-00002.wav",
    ]
    lines = [
        json.loads(x) for x in (session / "phrases.jsonl").read_text().splitlines()
    ]
    segs = [x for x in lines if x["type"] == "segment"]
    assert [x["gate"] for x in segs] == [GATE_DISAGREE, None]
    assert segs[1]["vosk"] == "добрий день" and segs[1]["vosk_conf"] == 1.0
    assert segs[1]["vosk_words"][0]["word"] == "добрий"
    (tr,) = [x for x in lines if x["type"] == "translation"]
    assert tr["mode"] == "two-text" and tr["text"] == "Добрий день."
    assert tr["hint"] == "добрий день" and tr["translation"] == "EN1."
    assert stats["session_record"]["on"] is True
    assert ts.get_stats()["session_record"]["on"] is False  # closed at stop
    assert any(e.get("status") == "session_record" and e["path"] for e in events)


def test_session_recording_is_off_by_default_and_toggles_live(tmp_path):
    asr = SlowAsr([AsrResult("Раз.", "uk"), AsrResult("Два.", "uk")], 0)
    vosk = FakeVosk(
        [VoskResult("раз", "uk", words("раз")), VoskResult("два", "uk", words("два"))]
    )
    ts, vads, capture, llm, _ = _recording_system(tmp_path, asr, vosk)
    assert ts.start()
    try:
        vads[-1].pending.append(seg())
        capture.push()
        assert wait_for(lambda: len(llm.calls) == 1)
        assert not tmp_path.exists() or not list(tmp_path.iterdir())
        ts.apply_settings(dataclasses.replace(ts.settings, record_session=True))
        vads[-1].pending.append(seg())
        capture.push()
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

    asr = SlowAsr([AsrResult("Раз.", "uk")], 0)
    tts = FakeTts()
    ts, vads, capture, llm, events = system(asr, None, use=ServiceUse(vosk=OFF))
    ts._pools.tts.client = tts
    stats = run(ts, vads, capture, [seg()], lambda: tts.calls)
    assert tts.warmups == 1 and [c[0] for c in tts.calls] == ["EN1."]
    assert stats["tts_warmup"]["error"] is None
    assert any(e.get("status") == "tts_warmup" for e in events)


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
