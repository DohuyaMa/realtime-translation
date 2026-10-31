"""AsrStreamSession (LocalAgreement-2) driven synchronously: no asyncio."""

import pytest

import src.services.asr.stream as st
from src.services.asr.backend import FakeAsrBackend, Segment, TimedWord
from src.services.asr.stream import AsrStreamSession
from src.services.asr.whisper_engine import SegmentStats
from svc_testutil import SR, pcm, tone

HALF = SR // 2


def speech(seconds: float) -> bytes:
    return pcm(tone(int(seconds * SR)))


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * SR) * 2)


def seg(text, start=0.0, step=0.3, stats=None):
    words = tuple(
        TimedWord(w, start + i * step, start + (i + 1) * step, 0.8)
        for i, w in enumerate(text.split())
    )
    end = words[-1].end if words else start
    return Segment(start, end, text, stats, words)


class Scripted(FakeAsrBackend):
    """stream_decode answers from a list of hypotheses (last one repeats)."""

    def __init__(self, hyps, **kw):
        super().__init__(**kw)
        self.hyps = list(hyps)
        self.prompts = []
        self.lengths = []

    def stream_decode(self, audio, language, prompt):
        self.prompts.append(prompt)
        self.lengths.append(len(audio))
        h = self.hyps.pop(0) if len(self.hyps) > 1 else self.hyps[0]
        return [seg(h)] if isinstance(h, str) else h


def session(backend, **kw):
    kw.setdefault("language", "en")
    kw.setdefault("languages", ("en", "uk"))
    return AsrStreamSession(backend, **kw)


def finals(msgs):
    return [m for m in msgs if m["type"] == "final"]


def run_ticks(s, n, chunk=0.5):
    out = []
    for _ in range(n):
        s.feed(speech(chunk))
        out += s.step()
    return out


def test_commits_longest_common_prefix_of_two_hypotheses():
    b = Scripted(
        ["hello", "hello world", "hello world how are", "hello world how are you"]
    )
    s = session(b)
    out = run_ticks(s, 4)
    texts = [m["text"] for m in finals(out)]
    assert texts == ["hello", "world", "how are"]
    assert all(not m["eos"] for m in finals(out))
    partials = [m["text"] for m in out if m["type"] == "partial"]
    assert partials[-1] == "you"
    assert [m["seq"] for m in finals(out)] == [1, 2, 3]


def test_agreement_ignores_case_and_punctuation_but_takes_the_newer_text():
    b = Scripted(["hello world", "Hello, world. Next"])
    s = session(b)
    out = run_ticks(s, 2)
    assert [m["text"] for m in finals(out)] == ["Hello, world."]


def test_last_word_of_the_newer_hypothesis_stays_open():
    b = Scripted(["one two", "one two.", "one two. three"])
    s = session(b)
    out = run_ticks(s, 2)
    assert [m["text"] for m in finals(out)] == ["one"]
    out = run_ticks(s, 1)
    assert [m["text"] for m in finals(out)] == ["two."]


def test_endpoint_commits_everything_up_to_speech_end():
    b = Scripted(["one two three"])
    s = session(b, t0=10.0)
    assert s.feed(speech(1.0)) is False
    assert finals(s.step()) == []  # first hypothesis: nothing agreed yet
    assert s.feed(silence(0.6)) is True
    out = s.step()
    (final,) = finals(out)
    assert final["text"] == "one two three" and final["eos"] is True
    assert final["start"] >= 10.0 and final["language"] == "en"
    (ep,) = [m for m in out if m["type"] == "endpoint"]
    assert ep["at"] == pytest.approx(11.0, abs=0.04)
    # decoded only up to the end of speech
    assert b.lengths[-1] <= int(1.0 * SR) + 3 * 512
    assert s.pending() is False


def test_short_click_is_not_speech_and_idle_buffer_keeps_only_preroll():
    b = Scripted(["x"])
    s = session(b)
    assert s.feed(speech(0.1)) is False
    assert s.feed(silence(2.0)) is False
    assert s.pending() is False and s.step() == []
    assert s.stats()["buffer_s"] <= 0.25
    assert b.lengths == []


def test_hypothesis_filters_hallucination_repetition_and_echo():
    bad = SegmentStats(no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=3.0)
    b = Scripted(
        [
            [seg("thanks for watching", 0.0), seg("good good good morning", 1.0)],
            [seg("good morning all", 1.0)],
        ]
    )
    s = session(b)
    out = run_ticks(s, 2)
    assert [m["text"] for m in finals(out)] == ["good morning"]
    assert s.stats()["dropped_segments"] == {"hallucination": 1}

    b2 = Scripted([[seg("so so so", 0.0, stats=bad)], [seg("a b c", 0.0)]])
    s2 = session(b2)
    run_ticks(s2, 2)
    assert s2.stats()["dropped_segments"] == {"low_confidence": 1}

    b3 = Scripted(["we met at the station yesterday"])
    s3 = session(b3, prompt="we met at the station yesterday")
    out = run_ticks(s3, 3)
    assert finals(out) == [] and s3.stats()["prompt_echo"] == 3


def test_prompt_is_committed_text_before_the_buffer_and_buffer_trims_at_sentence_end(
    monkeypatch,
):
    monkeypatch.setattr(st, "SENTENCE_TRIM_SECONDS", 0.0)
    b = Scripted(["Hi there. How", "Hi there. How are", "Hi there. How are"])
    s = session(b, prompt="ctx")
    out = run_ticks(s, 2)
    assert [m["text"] for m in finals(out)] == ["Hi there. How"]
    # trimmed at the end of "there." (0.6 s): the committed sentence is now
    # before the buffer, so it is the prompt; "How" is still in the buffer
    s.feed(speech(0.5))
    s.step()
    assert b.prompts[0] == "ctx"
    assert b.prompts[-1] == "ctx Hi there."
    assert b.lengths[-1] == int(1.5 * SR) - int(0.6 * SR)


def test_words_repeated_from_the_committed_tail_are_not_committed_twice(monkeypatch):
    monkeypatch.setattr(st, "SENTENCE_TRIM_SECONDS", 0.0)
    # after the trim whisper re-hears "How" at the buffer start
    b = Scripted(
        ["Hi there. How", "Hi there. How are", "How are you", "How are you doing"]
    )
    s = session(b)
    out = run_ticks(s, 4)
    text = " ".join(m["text"] for m in finals(out))
    assert text == "Hi there. How are you"


def test_short_buffer_is_not_trimmed_at_a_sentence_end():
    b = Scripted(["Hi there. How", "Hi there. How are", "Hi there. How are"])
    s = session(b)
    run_ticks(s, 3)
    assert b.lengths[-1] == int(1.5 * SR)  # < SENTENCE_TRIM_SECONDS


def test_auto_language_detected_once_with_enough_speech():
    b = Scripted(["a", "a b"], detected="uk")
    s = session(b, language=None, languages=("pl", "uk"))
    s.feed(speech(0.5))
    assert s.step() == [] and b.lengths == []  # < 1 s: not yet
    run_ticks(s, 2)
    assert s.language == "uk" and b.calls == []


def test_finish_commits_the_rest_as_eos():
    b = Scripted(["one", "one two", "one two three"])
    s = session(b)
    out = run_ticks(s, 2)
    assert [m["text"] for m in finals(out)] == ["one"]
    (last,) = s.finish()
    assert last["text"] == "two three" and last["eos"] is True
    assert s.finish() == []


def test_forced_commit_when_whisper_never_agrees(monkeypatch):
    monkeypatch.setattr(st, "MAX_BUFFER_SECONDS", 1.2)
    b = Scripted(["a", "b", "c", "d"])
    s = session(b)
    out = run_ticks(s, 3)
    assert [m["text"] for m in finals(out)] == ["c"]
    assert s.stats()["forced_commits"] == 1
    assert s.stats()["buffer_s"] < 1.5


def test_commit_lag_and_stats():
    b = Scripted(["one", "one two"])
    s = session(b)
    run_ticks(s, 2)
    st = s.stats()
    assert st["finals"] == 1 and st["committed_words"] == 1
    assert st["commit_lag_p50"] == pytest.approx(0.7, abs=0.01)  # 1.0 - 0.3


def test_odd_byte_frames_are_joined():
    b = Scripted(["x"])
    s = session(b)
    data = speech(0.5)
    s.feed(data[:1001])
    s.feed(data[1001:])
    assert s.audio_end == pytest.approx(0.5)


def test_warm_up_decodes_one_second_of_silence():
    b = Scripted(["x"])
    session(b, language=None, languages=("pl",)).warm_up()
    assert b.lengths == [SR] and b.prompts == [None]
