"""Keep the kit's .env from leaking between tests.

The kit's settings() copies .env into os.environ (slice/config.py), and some of
its tests do that at import time. Netra's settings read the same process
environment, so without this a key or database URL loaded by one kit test would
silently configure every Netra test collected after it. Each test starts from the
environment as it was before collection and ends by restoring it.
"""
import os

import pytest

_BEFORE_COLLECTION = dict(os.environ)
_PYTEST_OWN = {"PYTEST_CURRENT_TEST"}  # pytest sets and removes it around every test


def _restore() -> None:
    for key in set(os.environ) - set(_BEFORE_COLLECTION) - _PYTEST_OWN:
        del os.environ[key]
    os.environ.update(_BEFORE_COLLECTION)


@pytest.fixture(autouse=True)
def _environment_as_before_collection():
    _restore()
    yield
    _restore()
