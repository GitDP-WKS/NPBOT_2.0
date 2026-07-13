from __future__ import annotations

from fastapi.testclient import TestClient

from res_ai_v2.api import app


def test_health_endpoint_starts_with_database(temp_db) -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
