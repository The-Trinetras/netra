"""A scripted stand-in for slice.llm.complete: no key, no network, no tokens.

The same idea as demo/smoke/stub.py, but generic: a flow's tests hand it a script
of raw JSON replies per step, and the stub parses them through the REAL schema, so
a mistake in a schema fails here where it is cheap.

  stub = ScriptedModel({"draft": [RAW_1, RAW_2], "gate": [BLOCK, PASS]})
  flow = build_flow(call=stub)

It has the same keyword signature as complete(), records every call, and raises
when the flow asks for a reply the script does not have - which is usually the
finding (the flow made a call nobody expected), not a problem with the stub.
"""
from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel


class ScriptedModel:
    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script = {step: list(replies) for step, replies in script.items()}
        self._used: dict[str, int] = {}
        self.calls: list[str] = []
        self.messages: list[list[dict]] = []
        self.models: list[str | None] = []      # the model each call asked for, in order

    def __call__(self, *, settings, budget, messages, schema: Type[BaseModel] | None = None,
                 model: str | None = None, step: str = "call", timeout: float = 120.0) -> Any:
        base = step.split(":")[0]
        n = self._used.get(base, 0)
        self._used[base] = n + 1
        self.calls.append(step)
        self.messages.append(messages)
        self.models.append(model)

        try:
            raw = self._script[base][n]
        except (KeyError, IndexError):
            raise AssertionError(
                f"the script has no reply #{n} for step {step!r}. The flow made a call "
                "the script did not expect."
            )

        budget.record_tokens(len(raw) // 4)      # a plausible count, so the fences move
        return schema.model_validate_json(raw) if schema else raw

    def remaining(self) -> dict[str, int]:
        """Replies scripted but never asked for. A non-empty result after a run
        means the flow took a shorter path than the test expected."""
        return {step: len(replies) - self._used.get(step, 0)
                for step, replies in self._script.items()
                if len(replies) > self._used.get(step, 0)}
