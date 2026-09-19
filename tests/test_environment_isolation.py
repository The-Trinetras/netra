"""conftest.py gives every test the environment as it was before collection.

The first test does what the kit's settings() does with .env; the second is
any later test, which must not inherit it. Order matters: pytest runs a file's
tests in definition order.
"""
import os

PROBE = "NETRA_OPENROUTER_API_KEY"


def test_a_test_loads_a_key_into_the_process_environment():
    assert PROBE not in os.environ
    os.environ[PROBE] = "sk-or-leak-probe"


def test_the_next_test_does_not_inherit_it():
    assert PROBE not in os.environ
