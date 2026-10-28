"""Tests for the service metadata and health/readiness endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_returns_service_banner(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200

    body = response.json()
    assert body["service"]
    assert body["version"]
    assert body["endpoints"]["predict"] == "POST /api/v1/predict"
    assert body["docs"] == "/docs"


def test_health_is_ok_when_model_loaded(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_healthz_alias(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok_when_model_loaded(client: TestClient) -> None:
    assert client.get("/readyz").status_code == 200


def test_readyz_reports_503_without_model(
    missing_model_client: TestClient,
) -> None:
    response = missing_model_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["model_loaded"] is False
    assert response.json()["status"] == "degraded"


def test_predict_returns_503_without_model(
    missing_model_client: TestClient,
) -> None:
    response = missing_model_client.post(
        "/api/v1/predict", json={"url": "https://example.com"}
    )
    assert response.status_code == 503
    assert response.json()["error"] == "model_not_available"


def test_request_id_header_is_always_present(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_client_supplied_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-42"})
    assert response.headers["X-Request-ID"] == "trace-42"


def test_openapi_schema_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/api/v1/predict" in paths
    assert "/api/v1/predict/batch" in paths
    assert "/api/v1/model/info" in paths
