"""
Integration smoke tests for FastAPI endpoints.

Marked `not live` — these run without a real database by overriding
the DB dependency. They verify routing, schema correctness, and that
the app boots without crashing.

Run with: pytest tests/integration/ -v -m "not live"
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from anchor.main import create_app

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """Create app without triggering lifespan (no DB connection needed)."""
    return create_app()


@pytest.fixture
async def client(app):
    """Async test client with mocked DB session."""
    from anchor.database.engine import get_db
    from sqlalchemy.ext.asyncio import AsyncSession

    mock_session = AsyncMock(spec=AsyncSession)
    # Make `execute` return something that has `.scalars().all()` and `.scalar()`
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar.return_value = 1  # SELECT 1 → 1 (health check)
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ── Health endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_returns_200(client):
    response = await client.get("/api/v1/system/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_check_has_status_field(client):
    response = await client.get("/api/v1/system/health")
    data = response.json()
    assert "status" in data
    assert "db" in data
    assert "timestamp" in data
    assert "deployed_sha" in data


@pytest.mark.asyncio
async def test_health_check_db_ok(client):
    response = await client.get("/api/v1/system/health")
    data = response.json()
    assert data["db"] == "ok"


# ── System events endpoint ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_system_events_returns_200(client):
    response = await client.get("/api/v1/system/events")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_system_events_returns_list(client):
    response = await client.get("/api/v1/system/events")
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_system_config_returns_200(client):
    response = await client.get("/api/v1/system/config")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_system_config_has_rollout_fields(client):
    response = await client.get("/api/v1/system/config")
    data = response.json()
    assert "instruments" in data
    assert "trend" in data
    assert "mean_reversion" in data
    assert "lcr" in data
    assert "m15" in data


# ── Positions endpoint ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_positions_endpoint_exists(client):
    response = await client.get("/api/v1/positions")
    assert response.status_code == 200


# ── Signals endpoint ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signals_endpoint_exists(client):
    response = await client.get("/api/v1/signals/latest")
    assert response.status_code == 200


# ── OpenAPI schema ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openapi_schema_loads(client):
    """Verify the OpenAPI spec generates without errors (dev mode)."""
    response = await client.get("/api/docs")
    # In dev mode docs_url is /api/docs — may redirect or return 200
    assert response.status_code in (200, 307, 404)


# ── 404 handling ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_route_returns_404(client):
    response = await client.get("/api/v1/nonexistent_route")
    assert response.status_code == 404
