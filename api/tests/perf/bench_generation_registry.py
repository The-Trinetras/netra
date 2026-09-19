"""Generation registry retention: memory held and start() cost as sessions accumulate.

Not a pytest module. Run from the repository root:

    PYTHONPATH=api/src python api/tests/perf/bench_generation_registry.py --label <label> --out <dir>

In-process measurement of M1's GenerationRegistry only (tracemalloc for
memory, perf_counter for start()). Every navigation creates a generation, and
every app launch opens a new session, so sessions accumulate in a long-running
API process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from netra_api.speech.playback_metadata import DeliveredSentence, GenerationRegistry


async def fill(registry: GenerationRegistry, sessions: int, generations: int, sentences: int) -> list[float]:
    start_costs: list[float] = []
    for _ in range(sessions):
        session_id = uuid4()
        for index in range(generations):
            started = time.perf_counter()
            generation = registry.start(session_id, uuid4(), speakable=True)
            start_costs.append((time.perf_counter() - started) * 1e6)
            for sentence in range(sentences):
                registry.record_sentence(generation, DeliveredSentence(
                    segment_id=f"seg-{index}", sentence_id=f"s-{sentence}", origin="source_reading",
                    source_version_id=str(uuid4()), block_id=str(uuid4())))
            registry.complete(generation)
    return start_costs


async def measure(sessions: int, generations: int, sentences: int) -> dict:
    registry = GenerationRegistry()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    costs = await fill(registry, sessions, generations, sentences)
    held = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    return {"sessions": sessions, "generations_per_session": generations, "sentences_per_generation": sentences,
            "sessions_retained": len(registry._by_session), "bytes_held": held,
            "bytes_per_session_created": round(held / sessions),
            "start_us": {"median": round(statistics.median(costs), 3), "p99": round(sorted(costs)[int(0.99 * (len(costs) - 1))], 3)}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    runs = [asyncio.run(measure(3000, 40, 6)), asyncio.run(measure(3000, 40, 1))]
    # start() cost without tracemalloc overhead, three rounds
    timing = []
    for _ in range(3):
        registry = GenerationRegistry()
        costs = asyncio.run(fill(registry, 3000, 40, 1))
        timing.append(round(statistics.median(costs), 3))
    payload = {"label": args.label, "captured_at": datetime.now(timezone.utc).isoformat(),
               "python": sys.version.split()[0], "memory": runs, "start_us_median_untraced": timing}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"generation-registry-{args.label}.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    for run in runs:
        print(run)
    print("start() median us, untraced, 3 rounds:", timing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
