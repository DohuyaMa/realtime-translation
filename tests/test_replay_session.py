"""scripts/replay_session.py: a recorded session through the phonetic gate
and the sound-mode translation, against mocked services."""

import json

import numpy as np

from scripts.replay_session import load_session, replay, summary
from src.clients.asr import encode_wav
from src.clients.base import ServiceError
from test_phonetic_crosscheck import FakePhonetic, SoundLlm


def _session(tmp_path, units):
    lines = []
    for seq, (whisper, live) in enumerate(units, 1):
        name = f"seg-{seq:05d}.wav"
        (tmp_path / name).write_bytes(encode_wav(np.zeros(8000, np.int16), 16000))
        ws = whisper.split()
        lines.append(
            {
                "type": "segment",
                "seq": seq,
                "wav": name,
                "start_s": 10.0 * seq,
                "language": "uk",
                "whisper": whisper,
                "whisper_words": [
                    {
                        "word": w,
                        "start": 10.0 * seq + 0.1 * i,
                        "end": 10.0 * seq + 0.1 * (i + 1),
                    }
                    for i, w in enumerate(ws)
                ],
                # an old (Vosk) recording: extra fields are ignored
                "vosk": "x",
            }
        )
        lines.append(
            {"type": "translation", "seq": seq, "next": "", "translation": live}
        )
    (tmp_path / "phrases.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
    )
    return load_session(tmp_path)


def test_replay_gates_translates_retries_and_summarizes(tmp_path):
    segs = _session(
        tmp_path,
        [
            ("Добрий день.", "Good afternoon."),
            ("Дякую за перегляд!", "Thanks for watching!"),
            ("Нова модель.", "New model."),
            ("Раз.", "One."),
        ],
    )
    phonetic = FakePhonetic(
        ["добрий день", "", "нова модель", ServiceError("phonetic", "timeout", "x")]
    )
    llm = SoundLlm(
        replies=["Good day.", "The SOUND transcript says.", "New model.", "One."]
    )
    results = replay(segs, phonetic, llm, timeout_s=1.5)
    assert [r.gate for r in results] == ["agree", "no_word", "agree", None]
    assert [r.mode for r in results] == ["sound", None, "retry", "whisper-only"]
    assert llm.sounds == ["добрий день", "нова модель", None, None]
    assert results[2].rejected == ["meta"] and results[2].translation == "New model."
    assert results[0].live == "Good afternoon."
    s = summary(results, 1.5)
    assert s["dropped"] == 1 and s["translated"] == 3
    assert s["modes"] == {"sound": 1, "retry": 1, "whisper-only": 1}
    assert s["phonetic_errors"] == {"timeout": 1}
    assert s["phonetic_ms"]["over_budget"] == 0


def test_replay_gate_only(tmp_path):
    segs = _session(tmp_path, [("Добрий день.", "x")])
    results = replay(segs, FakePhonetic(["добрий день"]), None)
    assert results[0].gate == "agree" and results[0].translation is None
