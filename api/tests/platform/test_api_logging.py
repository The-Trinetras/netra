"""OPT-9: the API process configures the same JSON logging as the worker."""

import io
import json
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from netra_api.content.telemetry import log_event  # noqa: E402
from netra_api.main import create_app  # noqa: E402


def _composition():
    return SimpleNamespace(shutdown=lambda: None, registered={}, telemetry_diagnostics=dict, services=None, verifier=None, sources=None)


async def test_api_start_installs_json_logging_that_drops_secret_and_content_fields():
    root = logging.getLogger()
    before_handlers, before_level = list(root.handlers), root.level
    for handler in before_handlers:  # an earlier test may have configured logging already
        if getattr(handler, "_netra_handler", False):
            root.removeHandler(handler)
    app = create_app(composition=_composition())
    try:
        async with app.router.lifespan_context(app):
            added = [h for h in root.handlers if getattr(h, "_netra_handler", False)]
            assert len(added) == 1 and root.level == logging.INFO
            stream = io.StringIO()
            added[0].setStream(stream)
            log_event(logging.getLogger("netra_api.test"), "turn.done", component="api", request_id="r-1", token="t-secret", utterance_text="private")
            record = json.loads(stream.getvalue())
        assert record["event"] == "turn.done" and record["request_id"] == "r-1"
        assert "token" not in record and "utterance_text" not in record
        assert "t-secret" not in stream.getvalue() and "private" not in stream.getvalue()
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in before_handlers:
            root.addHandler(handler)
        root.setLevel(before_level)
