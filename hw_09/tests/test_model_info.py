"""Tests for GET /api/v1/model/info and the Prometheus endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_model_info_exposes_provenance(client: TestClient) -> None:
    response = client.get("/api/v1/model/info")
    assert response.status_code == 200

    body = response.json()
    assert body["model_version"] == "test-0.0.1"
    assert body["model_type"] == "sklearn.pipeline.Pipeline"
    # scikit-learn lazily exposes classes, so __module__ is the private module
    # (sklearn.ensemble._forest). The class name is the stable part.
    assert body["algorithm"].endswith("RandomForestClassifier")
    assert body["trained_at"]
    assert body["features"] > 0
    assert body["threshold"] == 0.5


def test_model_info_exposes_training_metrics(client: TestClient) -> None:
    metrics = client.get("/api/v1/model/info").json()["metrics"]
    assert set(metrics) >= {"precision", "recall", "f1", "auc"}
    assert all(0.0 <= value <= 1.0 for value in metrics.values())


def test_metrics_endpoint_exposes_prometheus_text(client: TestClient) -> None:
    client.post("/api/v1/predict", json={"url": "http://secure-login-bank.example.com"})
    client.post("/api/v1/predict", json={"url": "https://github.com/faizann24"})

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]

    body = response.text
    assert "url_fraud_predictions_total" in body


def test_metrics_counter_increments(client: TestClient) -> None:
    def total_predictions() -> float:
        """Sum every label series of the counter."""
        total = 0.0
        found = False
        for line in client.get("/metrics").text.splitlines():
            if line.startswith("url_fraud_predictions_total{"):
                total += float(line.rsplit(" ", 1)[1])
                found = True
        assert found, "counter not found in /metrics"
        return total

    before = total_predictions()
    client.post("/api/v1/predict", json={"url": "https://github.com/faizann24"})
    client.post("/api/v1/predict", json={"url": "https://github.com/faizann24"})
    assert total_predictions() == before + 2


def test_batch_counter_increments(client: TestClient) -> None:
    def batch_total() -> float:
        for line in client.get("/metrics").text.splitlines():
            if line.startswith("url_fraud_batches_total"):
                return float(line.rsplit(" ", 1)[1])
        raise AssertionError("batch counter not found in /metrics")

    before = batch_total()
    client.post(
        "/api/v1/predict/batch",
        json={"urls": ["https://github.com/faizann24", "docs.python.org"]},
    )
    assert batch_total() == before + 1
