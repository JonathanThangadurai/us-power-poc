from fastapi.testclient import TestClient

from app.main import app


def test_health_and_docs():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["node"] == "NP15"

        docs_resp = client.get("/docs")
        assert docs_resp.status_code == 200


def test_pipeline_status_shape():
    with TestClient(app) as client:
        resp = client.get("/pipeline/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "latest_run" in body
        assert "recent_runs" in body


def test_prices_rejects_unknown_market():
    with TestClient(app) as client:
        resp = client.get("/prices", params={"market": "BOGUS"})
        assert resp.status_code == 400
