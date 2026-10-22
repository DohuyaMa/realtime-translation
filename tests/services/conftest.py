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
