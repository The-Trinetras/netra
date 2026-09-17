# Local PostgreSQL

`docker-compose.yml` is the persistent developer database. The separate
`docker-compose.test.yml` service is PostgreSQL 17.11 backed by container
tmpfs, exposed only on localhost port 55432, and intended only for disposable
integration tests. Its checked-in password default is local-test-only and is
not used by any deployed environment.

Run the isolated integration suite from the repository root in PowerShell:

```powershell
docker compose -f infrastructure/compose/docker-compose.test.yml up -d --wait postgres-test
$env:DATABASE_URL = "postgresql+asyncpg://netra_test:netra_test_local_only@127.0.0.1:55432/netra_test"
uv run --locked alembic -c api/alembic.ini upgrade head
$testTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("netra-pytest-" + [guid]::NewGuid())
uv run --locked pytest -q -m integration --basetemp $testTemp
docker compose -f infrastructure/compose/docker-compose.test.yml down -v
Remove-Item Env:DATABASE_URL
```

Always stop the test service after the run. The production schema remains
owned by Alembic; Compose performs no migration automatically.
