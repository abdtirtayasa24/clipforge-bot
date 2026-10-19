from fastapi.testclient import TestClient

from clipforge.main import create_app


def test_live_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
