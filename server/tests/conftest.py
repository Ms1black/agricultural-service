import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN = "test-token"
WEB = ("admin", "secret")


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "plant.db"), token=TOKEN,
                    web_user=WEB[0], web_password=WEB[1])


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def device(app):
    """Клиент от имени устройства."""
    return TestClient(app, headers={"X-Api-Key": TOKEN})


@pytest.fixture
def web(app):
    """Клиент веб-интерфейса."""
    client = TestClient(app)
    client.auth = WEB
    return client


@pytest.fixture
def anon(app):
    return TestClient(app)


TELEMETRY = {"device": "plant1", "temp": 24.5, "air_hum": 55.2, "soil_raw": 2500,
             "soil_pct": 50, "soil_ok": True, "soil_dry": False}
