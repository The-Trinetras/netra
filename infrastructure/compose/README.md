# Local disposable test PostgreSQL

`docker-compose.yml` is intentionally unchanged: application database placement
(Compose PostgreSQL versus RDS/PgBouncer) is an open M1/M2 decision recorded in
`docs/team/handoffs/M2.md`. M2's RDS/PgBouncer Terraform proposal is kept on a local-only
`codex/m2-infra-proposal` branch and is not part of this change.

`docker-compose.test.yml` runs PostgreSQL 17.11 on container tmpfs, bound to
localhost port 55432, for **disposable integration tests only**. Its password
default is local-test-only and is never used by a deployed environment.

Run the database-backed suites from the repository root in PowerShell. The
tests never fall back to another database: without `NETRA_TEST_DATABASE_URL`
they skip.

```powershell
docker compose -f infrastructure/compose/docker-compose.test.yml up -d --wait postgres-test
$env:NETRA_DATABASE_URL = "postgresql+asyncpg://netra_test:netra_test_local_only@127.0.0.1:55432/netra_test"
$env:NETRA_TEST_DATABASE_URL = $env:NETRA_DATABASE_URL
uv run --locked alembic -c api/alembic.ini upgrade head
uv run --locked pytest -q -m integration
docker compose -f infrastructure/compose/docker-compose.test.yml down -v
Remove-Item Env:NETRA_DATABASE_URL, Env:NETRA_TEST_DATABASE_URL
```

Always stop the service afterwards (`down -v` discards the data). Alembic owns
the schema; Compose performs no migration.
