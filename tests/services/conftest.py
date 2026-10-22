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
    """Fresh RT_MODEL_DIR for slow tests; models download on first load.

    prepare_model re-enables downloads itself, so no pre-provisioning is needed.
    """
    from src.services.common.hf_env import set_online

    path = tmp_path_factory.mktemp("rt-models")
    mp = pytest.MonkeyPatch()
    for key in ("HF_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        mp.setenv(key, os.environ.get(key, ""))  # restored on undo
    mp.setenv("RT_MODEL_DIR", str(path))
    mp.setenv("RT_MODEL_DOWNLOAD", "true")
    yield path
    mp.undo()
    set_online()
