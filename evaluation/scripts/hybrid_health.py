"""Bounded, read-only health checks for local M2 evaluation dependencies."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from sqlalchemy import text

from netra_api.config import Settings
from netra_api.content.providers.pinecone import PineconeVectorIndex
from netra_api.content.providers.s3 import Boto3ObjectStorage
from netra_api.content.retrieval.embeddings import GeminiEmbeddingProvider
from netra_api.platform.database import create_engine

HealthOperation = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    required: bool
    error: str | None = None


async def _check(
    name: str,
    required: bool,
    operation: HealthOperation | None,
    *,
    timeout_seconds: float,
    missing_error: str = "not_configured",
) -> Check:
    if operation is None:
        return Check(name, False, required, missing_error)
    try:
        await asyncio.wait_for(operation(), timeout=timeout_seconds)
        return Check(name, True, required)
    except Exception as exc:
        # Exception type is enough for diagnosis and cannot contain a URL/token.
        return Check(name, False, required, type(exc).__name__)


async def _http_get(url: str, timeout_seconds: float) -> None:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()


async def run_checks(
    *,
    api_endpoint: str = "http://127.0.0.1:8000/health/live",
    s3_key: str | None = None,
    retrieval_mode: str = "hybrid",
    prometheus_endpoint: str | None = None,
    prometheus_required: bool = False,
    timeout_seconds: float = 10.0,
    operations: Mapping[str, HealthOperation | None] | None = None,
) -> list[Check]:
    """Check required local/AWS dependencies without mutating remote state.

    ``operations`` is an injection seam for unit tests; production callers use
    the existing Netra adapters constructed below.
    """

    if retrieval_mode not in {"hybrid", "lexical"}:
        raise ValueError("retrieval_mode must be hybrid or lexical")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    engine = None
    if operations is None:
        settings = Settings()
        engine = create_engine(settings)

        async def database() -> None:
            assert engine is not None
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        operations = {
            "api": lambda: _http_get(api_endpoint, timeout_seconds),
            "postgres": database,
            "s3": (
                (lambda: Boto3ObjectStorage(settings).get_object(s3_key))
                if s3_key
                else None
            ),
            "pinecone": lambda: PineconeVectorIndex(settings).query(
                settings.pinecone_namespace,
                [0.0] * settings.gemini_embedding_dimension,
                1,
                {"account_id": "netra-health-check"},
            ),
            "gemini_embedding": (
                lambda: GeminiEmbeddingProvider(settings).embed_query("Netra health check")
            ),
            "prometheus": (
                lambda: _http_get(
                    f"{prometheus_endpoint.rstrip('/')}/health", timeout_seconds
                )
                if prometheus_endpoint
                else None
            ),
        }

    specifications = [
        ("api", True, "api_endpoint_required"),
        ("postgres", True, "database_url_required"),
        ("s3", True, "s3_key_required"),
        ("pinecone", True, "pinecone_configuration_required"),
    ]
    if retrieval_mode == "hybrid":
        specifications.append(
            ("gemini_embedding", True, "embedding_configuration_required")
        )
    if prometheus_endpoint or prometheus_required:
        specifications.append(
            ("prometheus", prometheus_required, "prometheus_endpoint_required")
        )

    try:
        return [
            await _check(
                name,
                required,
                operations.get(name),
                timeout_seconds=timeout_seconds,
                missing_error=missing_error,
            )
            for name, required, missing_error in specifications
        ]
    finally:
        if engine is not None:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api-endpoint", default="http://127.0.0.1:8000/health/live"
    )
    parser.add_argument("--s3-key")
    parser.add_argument("--retrieval-mode", choices=("hybrid", "lexical"), default="hybrid")
    parser.add_argument("--prometheus-endpoint")
    parser.add_argument("--prometheus-required", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = asyncio.run(
        run_checks(
            api_endpoint=args.api_endpoint,
            s3_key=args.s3_key,
            retrieval_mode=args.retrieval_mode,
            prometheus_endpoint=args.prometheus_endpoint,
            prometheus_required=args.prometheus_required,
            timeout_seconds=args.timeout_seconds,
        )
    )
    payload = [asdict(item) for item in checks]
    print(
        json.dumps(payload, sort_keys=True)
        if args.json
        else "\n".join(
            f"{item['name']}: {'OK' if item['ok'] else item['error']} "
            f"({'required' if item['required'] else 'optional'})"
            for item in payload
        )
    )
    raise SystemExit(1 if any(not item.ok and item.required for item in checks) else 0)


if __name__ == "__main__":
    main()
