def test_docs_require_password(anon):
    for path in ("/web/api/docs", "/web/api/openapi.json"):
        r = anon.get(path)
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Basic")


def test_default_docs_are_off(web):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert web.get(path).status_code == 404


def test_swagger_ui(web):
    r = web.get("/web/api/docs")
    assert r.status_code == 200
    assert "swagger-ui" in r.text and "/web/api/openapi.json" in r.text


def test_openapi_schema(web):
    schema = web.get("/web/api/openapi.json").json()
    paths = schema["paths"]
    assert {"/api/telemetry", "/api/event", "/api/voice", "/web/api/state", "/health"} <= set(paths)
    assert "/web/api/docs" not in paths
    schemes = schema["components"]["securitySchemes"]
    assert schemes["APIKeyHeader"] == {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
    assert schemes["HTTPBasic"]["scheme"] == "basic"
