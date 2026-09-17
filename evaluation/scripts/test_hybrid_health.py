import asyncio

import pytest

from hybrid_health import run_checks


async def ok():
    return None


async def secret_failure():
    raise RuntimeError("postgresql://user:secret@example")


@pytest.mark.asyncio
async def test_hybrid_health_requires_every_query_path_dependency_and_redacts_errors():
    checks = await run_checks(
        s3_key="documents/source.pdf",
        operations={
            "api": ok,
            "postgres": secret_failure,
            "s3": ok,
            "pinecone": ok,
            "gemini_embedding": ok,
            "prometheus": None,
        },
    )
    assert [check.name for check in checks] == [
        "api",
        "postgres",
        "s3",
        "pinecone",
        "gemini_embedding",
    ]
    postgres = checks[1]
    assert not postgres.ok and postgres.required
    assert postgres.error == "RuntimeError"
    assert "secret" not in repr(checks)


@pytest.mark.asyncio
async def test_prometheus_is_optional_unless_explicitly_required():
    operations = {
        "api": ok,
        "postgres": ok,
        "s3": ok,
        "pinecone": ok,
        "gemini_embedding": ok,
        "prometheus": None,
    }
    optional = await run_checks(
        s3_key="x", prometheus_endpoint="http://judge", operations=operations
    )
    required = await run_checks(
        s3_key="x",
        prometheus_endpoint="http://judge",
        prometheus_required=True,
        operations=operations,
    )
    assert optional[-1].name == "prometheus" and not optional[-1].required
    assert required[-1].name == "prometheus" and required[-1].required


@pytest.mark.asyncio
async def test_lexical_health_does_not_claim_embedding_is_required():
    checks = await run_checks(
        s3_key="x",
        retrieval_mode="lexical",
        operations={"api": ok, "postgres": ok, "s3": ok, "pinecone": ok},
    )
    assert "gemini_embedding" not in {check.name for check in checks}


def test_health_timeout_is_bounded():
    async def slow():
        await asyncio.sleep(1)

    checks = asyncio.run(
        run_checks(
            s3_key="x",
            timeout_seconds=0.001,
            operations={
                "api": slow,
                "postgres": ok,
                "s3": ok,
                "pinecone": ok,
                "gemini_embedding": ok,
            },
        )
    )
    assert checks[0].error == "TimeoutError"
