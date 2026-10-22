from pathlib import Path

from scripts.gen_env_table import render_all

USAGE = Path(__file__).resolve().parents[1] / "docs" / "USAGE.md"


def test_usage_contains_generated_env_tables():
    out = render_all()
    assert "RT_ASR_CHUNK_SECONDS" in out and "RT_TTS_VOICE" in out
    assert out.strip() in USAGE.read_text()


def test_classes_are_assigned():
    out = render_all()
    assert "| `RT_PORT` | `8091` | BOOTSTRAP |" in out
    assert "| `RT_MODEL` |" in out and "RELOAD |" in out
    assert "| `RT_ASR_CHUNK_SECONDS` | `600.0` | TUNABLE |" in out
