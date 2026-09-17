from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient
import pytest

from netra_api.config import Settings
from netra_api.main import create_app


class FakeConnection:
    async def execute(self, statement):
        assert str(statement) == "SELECT 1"


class FakeEngine:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.disposed = False

    @asynccontextmanager
    async def connect(self):
        if not self.available:
            raise OSError("database unavailable")
        yield FakeConnection()


class FakeResources:
    def __init__(self, settings: Settings, *, available: bool = True) -> None:
        self.settings = settings
        self.engine = FakeEngine(available=available)
        self.sessions = object()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_application_starts_without_network_and_reports_liveness() -> None:
    resources = None

    def factory(settings):
        nonlocal resources
        resources = FakeResources(settings)
        return resources

    with TestClient(create_app(Settings(), resource_factory=factory)) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "live", "service": "netra-api"}
    assert resources is not None and resources.closed


def test_readiness_checks_postgresql_without_exposing_errors() -> None:
    app = create_app(
        Settings(),
        resource_factory=lambda settings: FakeResources(settings, available=False),
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"status": "unavailable", "dependency": "postgresql"}
    }
    assert "database unavailable" not in response.text


def test_invalid_application_database_scheme_fails_closed() -> None:
    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        create_app(Settings(database_url="sqlite+aiosqlite:///netra.db"))
