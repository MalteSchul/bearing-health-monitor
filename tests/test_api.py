from fastapi.testclient import TestClient

from monitor.api import create_app
from monitor.config import Settings


def make_client(**overrides) -> TestClient:
    return TestClient(create_app(Settings(**overrides)))


def test_health_endpoint_is_outside_versioned_api(tmp_path):
    client = make_client(frontend_dir=tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_is_served_from_configured_directory(tmp_path):
    (tmp_path / "index.html").write_text("<p>custom frontend</p>")
    client = make_client(frontend_dir=tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "custom frontend" in response.text


def test_static_frontend_does_not_shadow_api(tmp_path):
    (tmp_path / "index.html").write_text("<p>frontend</p>")
    client = make_client(frontend_dir=tmp_path)

    assert client.get("/api/health").json() == {"status": "ok"}


def test_missing_frontend_directory_still_serves_api(tmp_path):
    client = make_client(frontend_dir=tmp_path / "does-not-exist")

    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_cors_allows_configured_origin_only(tmp_path):
    client = make_client(frontend_dir=tmp_path, cors_origins=["http://localhost:5173"])

    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers


def test_version_reports_dev_outside_a_build(tmp_path, monkeypatch):
    monkeypatch.delenv("MONITOR_COMMIT_SHA", raising=False)
    client = make_client(frontend_dir=tmp_path)

    assert client.get("/api/version").json() == {"commit": "dev"}


def test_version_reports_the_build_commit(tmp_path):
    client = make_client(frontend_dir=tmp_path, commit_sha="3ef6878")

    assert client.get("/api/version").json() == {"commit": "3ef6878"}
    assert client.get("/openapi.json").json()["info"]["version"] == "3ef6878"
