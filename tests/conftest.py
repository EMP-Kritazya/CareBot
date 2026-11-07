import pytest


def pytest_collection_modifyitems(config, items):
    """Pi-only tests are skipped unless explicitly selected with `-m pi`."""
    if "pi" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="robot only: run with `python -m pytest -m pi` on the Pi")
    for item in items:
        if "pi" in item.keywords:
            item.add_marker(skip)
