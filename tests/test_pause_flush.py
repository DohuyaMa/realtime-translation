"""Owner 2026-10-31: the word count is a maximum, never awaited; a pause
flushes the phrase. Time here is the audio stream itself (32 ms VAD windows),
i.e. a deterministic fake clock."""

import json
from dataclasses import replace

import httpx
import numpy as np

from src.clients.llm import LlmClient
from src.core.runtime import LlmSettings
from src.segmentation import WINDOW_SAMPLES, PauseSegmenter, SegmenterConfig

WIN_MS = WINDOW_SAMPLES * 1000 // 16000  # 32


def feed_ms(seg, speech_ms, silence_ms):
    out = []
    for p, ms in ((0.9, speech_ms), (0.0, silence_ms)):
        for _ in range(-(-ms // WIN_MS)):
            window = np.full(WINDOW_SAMPLES, 1000 if p >= 0.5 else 0, np.int16)
            out.extend(seg.feed(window, p))
    return out


def llm(words_per_request):
    calls = []

    def handle(req):
        text = json.loads(req.content)["messages"][-1]["content"]
        calls.append(text)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    class Target:
        def current(self):
            return "http://w", "t"

        def invalidate(self, kind):
            pass

    client = LlmClient(
        Target(),
        LlmSettings(words_per_request=words_per_request),
        transport=httpx.MockTransport(handle),
    )
    return client, calls


def test_three_words_then_pause_flushed_without_waiting_for_fourth():
    cfg = SegmenterConfig.translate()  # end_silence_ms 700
    seg = PauseSegmenter(cfg)
    # ~1 s of speech (3 words), pause shorter than 700 ms: phrase still open
    assert feed_ms(seg, 1000, 500) == []
    # the pause reaches 700 ms: the phrase is emitted, no more speech needed
    (phrase,) = feed_ms(seg, 0, 250)
    assert phrase.reason == "end"
    # its 3 recognized words go to the LLM at once although the limit is 4
    client, calls = llm(4)
    assert client.translate("raz dwa trzy", "pl") == "raz dwa trzy"
    assert calls == ["raz dwa trzy"]


def test_shorter_pause_setting_flushes_sooner():
    seg = PauseSegmenter(replace(SegmenterConfig.translate(), end_silence_ms=300))
    (phrase,) = feed_ms(seg, 1000, 320)
    assert phrase.reason == "end"


def test_ten_words_limit_four_gives_4_4_2():
    client, calls = llm(4)
    client.translate("a1 a2 a3 a4 a5 a6 a7 a8 a9 a10", "pl")
    assert [len(c.split()) for c in calls] == [4, 4, 2]  # last one not held back
