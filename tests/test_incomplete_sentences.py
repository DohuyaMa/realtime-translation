"""Only complete sentences reach the LLM (live test 2026-10-31 §2, §4-§6):
an incomplete recognized tail is held and merged with the next segment;
the hold is bounded and every hold/merge/flush is counted."""

import time

import numpy as np

from src.clients.asr import AsrResult
from src.segmentation import Segment
from test_pipeline_stages import SlowAsr, SlowLlm, SlowTts, system
from test_translation_system import wait_for


def seg(start=0.0, end=1.0, reason="end"):
    audio = np.ones(int((end - start) * 16000) or 1, dtype=np.int16)
    return Segment(audio=audio, start_s=start, end_s=end, reason=reason)


class EchoLlm(SlowLlm):
    """English-looking output (the guard rejects Cyrillic)."""

    def translate_stream(self, text, source_lang, context=()):
        self.calls.append(text)
        yield f"EN{len(self.calls)}."


def run(texts, segments, *, speaking=False, after=None):
    played = []
    asr = SlowAsr([AsrResult(t, "uk") for t in texts], 0)
    llm = EchoLlm(0)
    ts, vads, capture = system(asr, llm, SlowTts(0), lambda a, sr: played.append(1))
    assert ts.start()
    vads[-1].speaking = speaking
    try:
        for s in segments:
            vads[-1].pending.append(s)
            capture.push()
            time.sleep(0.02)
        if after:
            after(ts, vads[-1], llm)
    finally:
        ts.stop()
    return llm.calls, ts.get_stats()


def test_pause_mid_sentence_is_merged_before_the_llm():
    calls, stats = run(
        ["Чикає ДОКИ", "До паузи, по факту."],
        [seg(0, 1), seg(1.5, 3)],
        after=lambda ts, v, llm: wait_for(lambda: llm.calls),
    )
    assert calls == ["Чикає ДОКИ До паузи, по факту."]
    assert stats["asr_merged"] == 1 and stats["asr_held_flushed"] == 0


def test_complete_sentences_go_at_once_and_the_tail_waits():
    def after(ts, vad, llm):
        assert wait_for(lambda: len(llm.calls) == 2)

    calls, stats = run(
        [
            "Не витрачайте час. Картки без фізичної карти, інформація про...",
            "Транспортний засіб, швидко заповнюйте форми.",
        ],
        [seg(0, 6), seg(6.5, 9)],
        after=after,
    )
    assert calls == [
        "Не витрачайте час.",
        "Картки без фізичної карти, інформація про... Транспортний засіб,"
        " швидко заповнюйте форми.",
    ]
    assert stats["asr_merged"] == 1


def test_cut_without_pause_holds_the_last_sentence():
    calls, _ = run(
        [
            "Можна обійтися без друку. Використовуйте екстремент.",
            "Приміток, щоб від руки підписати документи.",
        ],
        [seg(0, 12, "hard"), seg(12, 16)],
        after=lambda ts, v, llm: wait_for(lambda: len(llm.calls) == 2),
    )
    assert calls == [
        "Можна обійтися без друку.",
        "Використовуйте екстремент. Приміток, щоб від руки підписати документи.",
    ]


def test_held_text_is_sent_alone_when_no_speech_follows():
    t0 = time.monotonic()

    def after(ts, vad, llm):
        assert wait_for(lambda: llm.calls, timeout=3)

    calls, stats = run(["Чикає ДОКИ"], [seg()], after=after)
    assert calls == ["Чикає ДОКИ"]
    assert stats["asr_held_flushed"] == 1 and stats["asr_merged"] == 0
    # bounded by end_silence_ms (700 ms) plus polling, not by a long timeout
    assert time.monotonic() - t0 < 2.5


def test_hold_waits_while_the_continuation_is_spoken():
    def after(ts, vad, llm):
        time.sleep(1.2)  # > end_silence_ms, but speech is still in progress
        assert llm.calls == []
        vad.speaking = False
        assert wait_for(lambda: llm.calls)

    calls, stats = run(["Чикає ДОКИ"], [seg()], speaking=True, after=after)
    assert calls == ["Чикає ДОКИ"] and stats["asr_held_flushed"] == 1


def test_stop_sends_the_held_text():
    calls, stats = run(["Чикає ДОКИ"], [seg()], speaking=True)
    assert calls == ["Чикає ДОКИ"] and stats["asr_held_flushed"] == 1


def test_hold_never_spans_more_than_hard_max():
    # 20 s of speech without a sentence end: sent, not held again
    calls, stats = run(
        ["перша частина", "друга частина"],
        [seg(0, 11), seg(11, 20)],
        after=lambda ts, v, llm: wait_for(lambda: llm.calls),
    )
    assert calls == ["перша частина друга частина"]
    assert stats["asr_merged"] == 1 and stats["asr_held_flushed"] == 0
