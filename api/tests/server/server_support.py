"""Real-process helpers: seed M2 content through M2's repositories and serve the real app.

Everything here is real: PostgreSQL (disposable), the production composition
(`build_production`), uvicorn on a loopback port, HTTP and WebSocket over TCP.
Nothing calls a model provider; paths that need one are expected to fail
explicitly (PROVIDER_UNAVAILABLE) unless a test registers a controlled adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional
from uuid import UUID, uuid4

from netra_api.config import Settings
from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence
from netra_api.content.reading.postgres import AsyncReadingBlockRepository
from netra_api.content.retrieval.chunks import AsyncSearchChunkRepository, SearchChunk
from netra_api.content.sources.postgres import AsyncSourceRepository
from netra_api.identity.provisioning import provision
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory

OHM_SENTENCES = (
    ("Ohm's law relates voltage, current and resistance.", "It is written V = I × R."),
    ("Table 1 lists current and voltage.", "The rows are (1 A, 2 V), (2 A, 4 V) and (3 A, 6 V)."),
    ("The graph shows current on the x axis.", "Voltage is on the y axis."),
)


@dataclass
class SeededSource:
    account_id: UUID
    token: str
    source_id: UUID
    source_version_id: UUID
    block_ids: list[UUID]
    sentence_ids: list[list[UUID]]
    chunk_id: UUID


async def seed_account_and_source(database_url: str, title: str = "Ohm's law chapter") -> SeededSource:
    """Provision a credential and an ACTIVE source version with blocks and a chunk."""

    engine = create_engine(database_url)
    try:
        issued = await provision(engine, expires_in=timedelta(minutes=30))
        seeded = await _seed_source(create_session_factory(engine), issued.account_id, title, ready=True)
        return SeededSource(issued.account_id, issued.token, *seeded)
    finally:
        await engine.dispose()


async def seed_source_for_account(database_url: str, account_id: UUID, title: str, *, ready: bool = True) -> dict:
    """Add a source to an existing account: ready (active version with blocks) or not yet ready (no version)."""

    engine = create_engine(database_url)
    try:
        source_id, version_id, block_ids, sentence_ids, _ = await _seed_source(
            create_session_factory(engine), account_id, title, ready=ready)
        return {"source_id": source_id, "source_version_id": version_id, "block_ids": block_ids,
                "sentence_ids": sentence_ids}
    finally:
        await engine.dispose()


async def _seed_source(sessions, account_id: UUID, title: str, *, ready: bool):
    auth = AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))
    async with sessions() as session:
        sources = AsyncSourceRepository(session)
        source = await sources.create_source(auth, title)
        if not ready:
            return source.source_id, None, [], [], None
        version = await sources.create_version(
            auth, source.source_id, object_key=f"sources/{source.source_id}.pdf",
            content_hash=sources.content_hash_for(title.encode()), parser_name="pymupdf", parser_version="1.28.2")
        blocks, sentence_ids = [], []
        for ordinal, texts in enumerate(OHM_SENTENCES):
            ids = [uuid4() for _ in texts]
            sentence_ids.append(ids)
            blocks.append(ReadingBlock(
                block_id=uuid4(), source_version_id=version.source_version_id, ordinal=ordinal,
                block_type=BlockType.PARAGRAPH, locator=f"page {ordinal + 1}",
                sentences=[Sentence(sentence_id=i, ordinal=n, text=t) for n, (i, t) in enumerate(zip(ids, texts))]))
        await AsyncReadingBlockRepository(session).replace_blocks(version.source_version_id, blocks)
        chunk = SearchChunk(source_version_id=version.source_version_id,
                            text=" ".join(" ".join(t) for t in OHM_SENTENCES[1:2]),
                            block_ids=[blocks[1].block_id], embedding_version="none")
        await AsyncSearchChunkRepository(session).replace_chunks(version.source_version_id, [chunk])
        for stage in ("parsing", "blocks_built", "embedded", "projected"):
            await sources.mark_stage_complete(auth, version.source_version_id, stage)
        await sources.mark_ready(auth, version.source_version_id)
        await sources.activate_version(auth, source.source_id, version.source_version_id, 0)
    return source.source_id, version.source_version_id, [b.block_id for b in blocks], sentence_ids, chunk.chunk_id


@contextlib.asynccontextmanager
async def running_app(settings: Settings, app: Any = None) -> AsyncIterator[str]:
    """Serve the real ASGI app with uvicorn on 127.0.0.1:<free port>; yield the base URL."""

    import uvicorn

    from netra_api.main import create_app

    app = app or create_app(settings=settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on",
                                           ws="websockets-sansio"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.025)
    if not server.started:
        task.cancel()
        raise RuntimeError("uvicorn did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


class ProtocolClient:
    """Minimal protocol v1 client over a real WebSocket connection."""

    def __init__(self, socket: Any, session_id: UUID) -> None:
        self.socket, self.session_id, self.sequence = socket, session_id, 0
        self.received: list[dict[str, Any]] = []
        self.binary: list[bytes] = []

    async def send(self, message_type: str, payload: dict[str, Any], request_id: Optional[UUID] = None) -> UUID:
        request_id = request_id or uuid4()
        self.sequence += 1
        await self.socket.send(json.dumps({
            "protocol_version": "1.0", "message_id": str(uuid4()), "session_id": str(self.session_id),
            "request_id": str(request_id), "sequence": self.sequence, "type": message_type, "payload": payload}))
        return request_id

    async def until(self, request_id: UUID, *types: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Collect frames for request_id until one of ``types`` (or an error) arrives."""

        deadline = asyncio.get_running_loop().time() + timeout
        mine: list[dict[str, Any]] = []
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {types}; got {[m['type'] for m in mine]}")
            frame = await asyncio.wait_for(self.socket.recv(), remaining)
            if isinstance(frame, bytes):
                self.binary.append(frame)
                continue
            message = json.loads(frame)
            self.received.append(message)
            if message["request_id"] == str(request_id):
                mine.append(message)
                if message["type"] in types or message["type"] == "error":
                    return mine

    async def settle(self, request_id: UUID, quiet: float = 0.4, timeout: float = 5.0) -> list[dict[str, Any]]:
        """All frames for request_id, once none has arrived for ``quiet`` seconds."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        mine: list[dict[str, Any]] = []
        while loop.time() < deadline:
            try:
                frame = await asyncio.wait_for(self.socket.recv(), quiet if mine else deadline - loop.time())
            except asyncio.TimeoutError:
                if mine:
                    return mine
                break
            if isinstance(frame, bytes):
                self.binary.append(frame)
                continue
            message = json.loads(frame)
            self.received.append(message)
            if message["request_id"] == str(request_id):
                mine.append(message)
        if not mine:
            raise AssertionError("no frames arrived for the request")
        return mine
