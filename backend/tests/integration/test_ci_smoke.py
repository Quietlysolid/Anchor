import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock

from anchor.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    from anchor.database.engine import get_db
    from sqlalchemy.ext.asyncio import AsyncSession

    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar.return_value = 1
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_broker_calls(monkeypatch):
    from anchor.execution.ibkr_client import IBKRBrokerClient

    monkeypatch.setattr(IBKRBrokerClient, "get_open_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(IBKRBrokerClient, "get_pending_orders", AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_health_endpoint_returns_deploy_fields(client):
    response = await client.get("/api/v1/system/health")
    assert response.status_code == 200

    data = response.json()
    assert data["db"] == "ok"
    assert "status" in data
    assert "timestamp" in data
    assert "deployed_sha" in data


@pytest.mark.asyncio
async def test_system_config_returns_futures_rollout_fields(client):
    response = await client.get("/api/v1/system/config")
    assert response.status_code == 200

    data = response.json()
    assert "account_mode" in data
    assert "account_environment" in data
    assert "instruments" in data

