"""Serve the real API for the desktop client's live tests. Local, disposable database only.

Not a pytest module. Run from the repository root with a disposable database:

    PYTHONPATH="api/src;worker/src" python api/tests/server/serve_for_client.py \
        --database-url "$NETRA_TEST_DATABASE_URL" --info-file <scratch>/live-server.json

It seeds one account with two ready sources and one that is not ready yet,
plus a second account's source that must stay invisible, and a credential for
the first account that expires within seconds. It serves ``create_app`` on
127.0.0.1 with uvicorn and writes the WebSocket endpoint, the test tokens and
the seeded identifiers to ``--info-file`` for ``NETRA_LIVE_SERVER_INFO``.

What is real: PostgreSQL, the production composition (M2 reading, sessions,
identity, stored-credential auth), uvicorn, HTTP and WebSocket over TCP, and
M1's SpeechOutput framing/fencing. What is NOT real: the synthesizer. It is a
paced stand-in producing opaque bytes labelled audio/mpeg, so audio frames,
STOP and late-frame fencing can be exercised; nothing here is audible or a
claim about a speech provider. The token is a test credential on a disposable
database and is written only to the local info file.

Completed-audio caching is OFF by default (``--audio-cache`` turns it on): the
server outlives individual client test runs, and a cached segment is sent whole
in one frame, so later runs would no longer exercise paced multi-frame
delivery, mid-stream STOP or a mid-stream drop. Caching itself is M1 behaviour
covered by the Python speech tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from netra_api.bootstrap import build_production
from netra_api.config import Settings
from netra_api.identity.provisioning import provision
from netra_api.main import create_app
from netra_api.platform.database import create_engine
from netra_api.speech.quota import InMemoryQuotaLedger
from netra_api.speech.synthesis import InMemoryAudioCache, SpeechOutput, SynthesisConfig

from server_support import running_app, seed_account_and_source, seed_source_for_account


class NoStoreAudioCache:
    """Never stores: every segment is synthesized and paced (see module docstring)."""

    async def get(self, key):
        return None

    async def put(self, key, audio):
        return None


class PacedStandInSynthesizer:
    """Opaque chunks at a fixed pace, so a STOP can land mid-generation."""

    config = SynthesisConfig(provider="stand-in", model_id="stand-in", voice_id="stand-in", media_type="audio/mpeg")

    def __init__(self, chunks: int, delay_seconds: float) -> None:
        self.chunks, self.delay_seconds = chunks, delay_seconds

    async def stream(self, text: str):
        for index in range(self.chunks):
            await asyncio.sleep(self.delay_seconds)
            yield f"{len(text)}:{index};".encode() * 64


async def serve(database_url: str, info_file: Path, lifetime_seconds: float, chunks: int, delay: float,
                audio_cache: bool) -> None:
    seeded = await seed_account_and_source(database_url)
    second = await seed_source_for_account(database_url, seeded.account_id, "Kirchhoff's laws")
    not_ready = await seed_source_for_account(database_url, seeded.account_id, "Still processing notes", ready=False)
    other = await seed_account_and_source(database_url, title="another student's notes")
    engine = create_engine(database_url)
    try:
        expiring = await provision(engine, expires_in=timedelta(seconds=1), account_id=seeded.account_id)
    finally:
        await engine.dispose()
    settings = Settings(database_url=database_url, auth_mode="stored_credential", trace_to_log=False)
    composition = build_production(settings)
    composition.services.speech = SpeechOutput(
        PacedStandInSynthesizer(chunks, delay), InMemoryQuotaLedger(characters_per_account=1_000_000),
        InMemoryAudioCache() if audio_cache else NoStoreAudioCache(), composition.services.generations)
    async with running_app(settings, app=create_app(composition=composition)) as host:
        info = {
            "ws_endpoint": f"ws://{host}/v1/ws",
            "token": seeded.token,
            "source_id": str(seeded.source_id),
            "source_version_id": str(seeded.source_version_id),
            "title": "Ohm's law chapter",
            "other_source_id": str(other.source_id),
            "first_sentence_ids": [str(i) for i in seeded.sentence_ids[0]],
            "second_source_id": str(second["source_id"]),
            "second_source_version_id": str(second["source_version_id"]),
            "second_first_sentence_ids": [str(i) for i in second["sentence_ids"][0]],
            "not_ready_source_id": str(not_ready["source_id"]),
            "expired_token": expiring.token,
        }
        info_file.write_text(json.dumps(info), encoding="utf-8")
        print(f"serving on {host}", flush=True)
        await asyncio.sleep(lifetime_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--info-file", required=True, type=Path)
    parser.add_argument("--lifetime-seconds", type=float, default=600)
    parser.add_argument("--chunks", type=int, default=8)
    parser.add_argument("--chunk-delay-seconds", type=float, default=0.15)
    parser.add_argument("--audio-cache", action="store_true", help="enable M1's in-memory completed-audio cache")
    args = parser.parse_args()
    if "127.0.0.1" not in args.database_url and "localhost" not in args.database_url:
        sys.exit("refusing: the database must be a local disposable one")
    asyncio.run(serve(args.database_url, args.info_file, args.lifetime_seconds, args.chunks, args.chunk_delay_seconds,
                      args.audio_cache))


if __name__ == "__main__":
    main()
