import subprocess
import sys

import numpy as np

from src.services.asr.chunking import split_for_transcription


def test_short_audio_not_split():
    out = split_for_transcription(
        np.zeros(16000 * 20, np.float32), 600, 30, spans_fn=lambda a: []
    )
    assert out == [(0, 320000)]


def test_split_uses_pause_from_spans():
    a = np.zeros(16000 * 1300, np.float32)
    spans = [(0, 16000 * 590), (16000 * 595, 16000 * 1300)]
    out = split_for_transcription(a, 600, 30, spans_fn=lambda _: spans)
    assert out[0][1] == 16000 * 590 + (16000 * 5) // 2


def test_faster_whisper_not_imported_at_module_import():
    code = (
        "import sys, src.services.asr.chunking;"
        "assert 'faster_whisper' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
