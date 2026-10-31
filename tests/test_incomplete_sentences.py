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

    def translate_stream(self, text, source_lang, context=(), check=None):
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


def test_cut_with_a_full_stop_goes_at_once():
    # live test §11 #8/#9 (rt-app log 21:08:57): the segment was cut without
    # a pause, whisper ended both sentences with a full stop; the second one
    # waited 9 s for the next segment although it was complete
    calls, stats = run(
        [
            "Підписуйте і доповнюйте примітками файли PDF. Коли вам"
            " знадобиться підписати файл PDF, можна обійтися без завантаження"
            " і друку.",
            "Використовуйте інструмент приміток, щоб від руки підписати"
            " документи, заповнити форму і виділити рядки.",
        ],
        [seg(0, 6, "soft"), seg(6, 12, "hard")],
        after=lambda ts, v, llm: wait_for(lambda: len(llm.calls) == 2),
    )
    assert calls == [
        "Підписуйте і доповнюйте примітками файли PDF. Коли вам знадобиться"
        " підписати файл PDF, можна обійтися без завантаження і друку.",
        "Використовуйте інструмент приміток, щоб від руки підписати документи,"
        " заповнити форму і виділити рядки.",
    ]
    assert stats["asr_merged"] == 0 and stats["asr_held_flushed"] == 0


def test_cut_ending_with_an_ellipsis_is_still_held():
    calls, stats = run(
        ["Платформу Гараш між бар'єрами, агенція та...", "сайти і веб-додатки."],
        [seg(0, 6, "hard"), seg(6, 8)],
        after=lambda ts, v, llm: wait_for(lambda: llm.calls),
    )
    assert calls == [
        "Платформу Гараш між бар'єрами, агенція та... сайти і веб-додатки."
    ]
    assert stats["asr_merged"] == 1


# live test §12 (rt-app log 21:14:49-21:15:17)
S16 = (
    "Кіберспортивний додаток для прогнозів на матчі Трімове ТВА. Платформу"
    " Гараш між бар'єрами, агенція та..."
)
S18 = "сайти і веб-додатки для бізнесу, плюс авто-тестер, плейврайд..."
S20 = "Платформу Гараш між бар'єрами, агенція та..."  # echo of #16


def test_whisper_echo_of_previous_text_is_dropped_and_releases_the_tail():
    """§12 #20: whisper repeated #16 from {{previous_text}} on noise; with
    Vosk off nothing else catches it. The drop also releases the held tail
    at once (the VAD keeps reporting speech, so nothing else would)."""
    t0 = time.monotonic()
    asr_calls = []

    def after(ts, vad, llm):
        assert wait_for(lambda: len(llm.calls) == 2, timeout=2)
        asr_calls.extend(ts._pools.asr.client.calls)

    calls, stats = run(
        [S16, S18, S20], [seg(0, 6), seg(6, 9), seg(9, 12)], speaking=True, after=after
    )
    assert calls == [
        "Кіберспортивний додаток для прогнозів на матчі Трімове ТВА.",
        f"{S20} {S18}",
    ]
    assert stats["asr_echo"] == 1 and stats["asr_held_flushed"] == 1
    assert S20 in asr_calls[2]["prompt"]  # it was in the prompt it echoed
    assert time.monotonic() - t0 < 2.0  # not the 6.7 s hold cap


def test_repeated_short_phrase_is_not_an_echo():
    calls, stats = run(
        ["Так, дякую.", "Так, дякую."],
        [seg(0, 1), seg(2, 3)],
        after=lambda ts, v, llm: wait_for(lambda: len(llm.calls) == 2),
    )
    assert calls == ["Так, дякую.", "Так, дякую."] and stats["asr_echo"] == 0


def test_segment_without_words_releases_the_held_tail():
    calls, stats = run(
        [S18, "..."],
        [seg(0, 3), seg(3, 4)],
        speaking=True,
        after=lambda ts, v, llm: wait_for(lambda: llm.calls, timeout=1),
    )
    assert calls == [S18] and stats["asr_held_flushed"] == 1


def test_held_tail_waits_at_most_end_silence_plus_soft_max():
    """§12: a tail waited 20 s because the VAD stayed "in speech"; the cap
    is end_silence_ms + soft_max_ms (here 200 + 600 ms)."""
    import dataclasses

    from src.core.runtime import SegmentationSettings
    from src.segmentation import SegmenterConfig

    cfg = dataclasses.replace(
        SegmenterConfig.translate(), end_silence_ms=200, soft_max_ms=600
    )

    def after(ts, vad, llm):
        assert not wait_for(lambda: llm.calls, timeout=0.5)
        assert wait_for(lambda: llm.calls, timeout=1.5)

    asr = SlowAsr([AsrResult(S18, "uk")], 0)
    llm = EchoLlm(0)
    ts, vads, capture = system(asr, llm, SlowTts(0), lambda a, sr: None)
    ts.settings = dataclasses.replace(
        ts.settings, segmentation=SegmentationSettings(translate=cfg)
    )
    assert ts.start()
    vads[-1].speaking = True
    try:
        vads[-1].pending.append(seg(0, 3))
        capture.push()
        after(ts, vads[-1], llm)
    finally:
        ts.stop()
    assert llm.calls == [S18] and ts.get_stats()["asr_held_flushed"] == 1


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
