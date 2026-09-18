"""Serve the real API for the desktop client's live tests. Local, disposable database only.

Not a pytest module. Run from the repository root with a disposable database:

    PYTHONPATH="api/src;worker/src" python api/tests/server/serve_for_client.py \
        --database-url "$NETRA_TEST_DATABASE_URL" --info-file <scratch>/live-server.json

It seeds one account with a ready, active source (plus a second account's
source that must stay invisible), serves ``create_app`` on 127.0.0.1 with
uvicorn, and writes the WebSocket endpoint, the provisioned test token and the
seeded identifiers to ``--info-file`` for ``NETRA_LIVE_SERVER_INFO``.

What is real: PostgreSQL, the production composition (M2 reading, sessions,
identity, stored-credential auth), uvicorn, HTTP and WebSocket over TCP, and
M1's SpeechOutput framing/fencing. What is NOT real: the synthesizer. It is a
paced stand-in producing opaque bytes labelled audio/mpeg, so audio frames,
STOP and late-frame fencing can be exercised; nothing here is audible or a
claim about a speech provider. The token is a test credential on a disposable
database and is written only to the local info file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from netra_api.bootstrap import build_production
from netra_api.config import Settings
from netra_api.main import create_app
from netra_api.speech.quota import InMemoryQuotaLedger
from netra_api.speech.synthesis import InMemoryAudioCache, SpeechOutput, SynthesisConfig

from server_support import running_app, seed_account_and_source


class PacedStandInSynthesizer:
    """Opaque chunks at a fixed pace, so a STOP can land mid-generation."""

    config = SynthesisConfig(provider="stand-in", model_id="stand-in", voice_id="stand-in", media_type="audio/mpeg")

    def __init__(self, chunks: int, delay_seconds: float) -> None:
        self.chunks, self.delay_seconds = chunks, delay_seconds

    async def stream(self, text: str):
        for index in range(self.chunks):
            await asyncio.sleep(self.delay_seconds)
            yield f"{len(text)}:{index};".encode() * 64


async def serve(database_url: str, info_file: Path, lifetime_seconds: float, chunks: int, delay: float) -> None:
    seeded = await seed_account_and_source(database_url)
    other = await seed_account_and_source(database_url, title="another student's notes")
    settings = Settings(database_url=database_url, auth_mode="stored_credential", trace_to_log=False)
    composition = build_production(settings)
    composition.services.speech = SpeechOutput(
        PacedStandInSynthesizer(chunks, delay), InMemoryQuotaLedger(characters_per_account=1_000_000),
        InMemoryAudioCache(), composition.services.generations)
    async with running_app(settings, app=create_app(composition=composition)) as host:
        info = {
            "ws_endpoint": f"ws://{host}/v1/ws",
            "token": seeded.token,
            "source_id": str(seeded.source_id),
            "source_version_id": str(seeded.source_version_id),
            "title": "Ohm's law chapter",
            "other_source_id": str(other.source_id),
            "first_sentence_ids": [str(i) for i in seeded.sentence_ids[0]],
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
    args = parser.parse_args()
    if "127.0.0.1" not in args.database_url and "localhost" not in args.database_url:
        sys.exit("refusing: the database must be a local disposable one")
    asyncio.run(serve(args.database_url, args.info_file, args.lifetime_seconds, args.chunks, args.chunk_delay_seconds))


if __name__ == "__main__":
    main()
