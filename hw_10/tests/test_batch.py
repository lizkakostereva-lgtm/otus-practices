"""Tests for the batch endpoint and its limits."""

from __future__ import annotations

from fastapi.testclient import TestClient

URLS = [
    "http://secure-login-bank.example.com/verify",
    "https://github.com/faizann24",
    "docs.python.org",
]


def test_batch_returns_one_prediction_per_url(client: TestClient) -> None:
    response = client.post("/api/v1/predict/batch", json={"urls": URLS})
    assert response.status_code == 200

    body = response.json()
    assert body["count"] == len(URLS)
    assert len(body["predictions"]) == len(URLS)
    assert [p["url"] for p in body["predictions"]] == [
        "http://secure-login-bank.example.com/verify",
        "https://github.com/faizann24",
        "http://docs.python.org",
    ]
    assert body["request_id"]


def test_batch_labels_match_single_predictions(client: TestClient) -> None:
    """Batch and single scoring must agree — same model, same threshold."""
    batch = client.post("/api/v1/predict/batch", json={"urls": URLS}).json()
    for prediction in batch["predictions"]:
        single = client.post("/api/v1/predict", json={"url": prediction["url"]}).json()
        assert single["prediction"]["probability"] == prediction["probability"]
        assert single["prediction"]["is_fraud"] == prediction["is_fraud"]


def test_batch_threshold_override_applies_to_every_item(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/predict/batch", json={"urls": URLS, "threshold": 1.0}
    )
    assert response.status_code == 200
    assert all(p["is_fraud"] is False for p in response.json()["predictions"])
    assert all(p["threshold"] == 1.0 for p in response.json()["predictions"])


def test_batch_rejects_empty_list(client: TestClient) -> None:
    assert client.post("/api/v1/predict/batch", json={"urls": []}).status_code == 422


def test_batch_rejects_invalid_url_inside_list(client: TestClient) -> None:
    response = client.post(
        "/api/v1/predict/batch", json={"urls": ["https://ok.io", "not a url"]}
    )
    assert response.status_code == 422


def test_batch_enforces_max_batch_size(client: TestClient) -> None:
    urls = [f"https://example{i}.io" for i in range(101)]
    assert client.post("/api/v1/predict/batch", json={"urls": urls}).status_code == 422
