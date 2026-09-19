"""langgraph-checkpoint-postgres==3.1.2 over psycopg on the disposable PostgreSQL.

Checkpoint storage is kept out of the application schema (runtime baseline:
"Keep checkpoint setup separate from application migration ownership"), so
this uses its own disposable database, NETRA_TEST_CHECKPOINT_DATABASE_URL
(plain ``postgresql://`` for psycopg, never the asyncpg application URL).

It proves the pinned saver, psycopg and PostgreSQL 17.11 work together:
``setup()``, a persisted checkpoint, and resume after a crash without
re-running the completed node. psycopg's async mode needs a selector event
loop on Windows, so the test runs its own loop.
"""

import asyncio
import os
from typing import TypedDict
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


class _State(TypedDict, total=False):
    prepared: int
    effects: list[str]


def test_a_crashed_graph_resumes_from_its_postgres_checkpoint():
    url = os.environ.get("NETRA_TEST_CHECKPOINT_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_CHECKPOINT_DATABASE_URL (a disposable checkpoint database) is not set")

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph

    calls = {"prepare": 0, "effect": 0}

    async def prepare(state: _State) -> dict:
        calls["prepare"] += 1
        return {"prepared": state.get("prepared", 0) + 1}

    async def effect(state: _State) -> dict:
        calls["effect"] += 1
        if calls["effect"] == 1:
            raise ConnectionError("simulated crash after the checkpoint of prepare")
        return {"effects": ["done"]}

    async def main() -> None:
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            builder = StateGraph(_State)
            builder.add_node("prepare", prepare)
            builder.add_node("effect", effect)
            builder.add_edge(START, "prepare")
            builder.add_edge("prepare", "effect")
            builder.add_edge("effect", END)
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": f"integration-{uuid4()}"}}

            with pytest.raises(ConnectionError):
                await graph.ainvoke({"prepared": 0}, config)
            snapshot = await graph.aget_state(config)
            assert snapshot.values["prepared"] == 1 and snapshot.next == ("effect",)

            final = await graph.ainvoke(None, config)  # resume from the stored checkpoint
            assert final == {"prepared": 1, "effects": ["done"]}
            assert calls == {"prepare": 1, "effect": 2}  # the completed node did not run again
            assert len([c async for c in saver.alist(config)]) >= 3

    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
