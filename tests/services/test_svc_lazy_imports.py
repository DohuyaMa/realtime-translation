"""Heavy ML libraries are imported only by the background model loader.

(/health must answer fast.)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import sys
from fastapi.testclient import TestClient
from src.services.asr.app import create_app as asr_app
from src.services.asr.settings import AsrSettings
from src.services.tts.app import create_app as tts_app
from src.services.tts.settings import TtsSettings

# Real backends selected (defaults). TestClient without `with` does not run the
# lifespan, so the loader thread never starts: everything imported here is
# import-time cost.
# Backend modules themselves must also import cheaply (heavy libs only inside load()).
import src.services.asr.faster_whisper_backend  # noqa: F401
import src.services.asr.whisper_engine  # noqa: F401
import src.services.tts.kokoro_backend  # noqa: F401
import src.services.tts.kokoro_onnx  # noqa: F401
import src.services.common.model_store  # noqa: F401

for app in (asr_app(AsrSettings.from_env({})), tts_app(TtsSettings.from_env({}))):
    assert TestClient(app).get("/health").json() == {
        "status": "ok", "model": {"loaded": False, "state": "unloaded"}
    }
heavy = (
    "torch", "ctranslate2", "faster_whisper", "transformers", "kokoro", "misaki",
    "sounddevice", "scipy", "soundfile", "onnxruntime", "huggingface_hub",
)
print("HEAVY=" + ",".join(m for m in heavy if m in sys.modules))
"""


def test_creating_apps_does_not_import_heavy_libraries():
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "HEAVY=", out.stdout
