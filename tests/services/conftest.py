import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: real-model test; run with RT_SLOW_TESTS=1"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RT_SLOW_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="real-model test; set RT_SLOW_TESTS=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def real_model_dir(tmp_path_factory):
    """Fresh RT_MODEL_DIR for slow tests.

    Both pinned models are provisioned up front: the first backend load flips
    HF_HUB_OFFLINE=1 process-wide, so a later download would fail.
    """
    from src.services.common.hf_env import configure_hf_home
    from src.services.common.model_store import ModelSettings, resolve_model

    path = tmp_path_factory.mktemp("rt-models")
    mp = pytest.MonkeyPatch()
    for key in ("HF_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        mp.setenv(key, os.environ.get(key, ""))  # restored on undo
    mp.delenv("HF_HUB_OFFLINE")
    mp.setenv("RT_MODEL_DIR", str(path))
    mp.setenv("RT_MODEL_DOWNLOAD", "true")
    settings = ModelSettings.from_env()
    configure_hf_home(settings.model_dir)
    for alias in ("whisper-small", "kokoro-82m-v1.0-onnx"):
        resolve_model(alias, settings.store(), download=True, token=settings.token())
    yield path
    mp.undo()
