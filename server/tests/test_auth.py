import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .conftest import TELEMETRY


def test_health_is_public(anon):
    r = anon.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_device_api_requires_token(anon):
    assert anon.post("/api/telemetry", json=TELEMETRY).status_code == 401
    r = anon.post("/api/telemetry", json=TELEMETRY, headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/web/api/auth", "/web/api/state", "/web/api/export.csv"])
def test_web_requires_password(anon, path):
    r = anon.get(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


def test_web_rejects_wrong_password(app):
    client = TestClient(app)
    client.auth = ("admin", "nope")
    assert client.get("/web/api/auth").status_code == 401


def test_web_with_password(web):
    assert web.get("/web/api/auth").status_code == 204


def test_device_token_does_not_open_web(device):
    assert device.get("/web/api/state").status_code == 401


def test_refuses_to_start_without_secrets(tmp_path):
    with pytest.raises(RuntimeError, match="PLANT_TOKEN"):
        create_app(Settings(db_path=str(tmp_path / "x.db")))


def test_insecure_mode_for_local_dev(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "x.db"), insecure=True))
    c = TestClient(app)
    assert c.post("/api/telemetry", json=TELEMETRY).status_code == 200
    assert c.get("/web/api/auth").status_code == 204
