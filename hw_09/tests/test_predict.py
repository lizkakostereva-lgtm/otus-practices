"""Tests for POST /api/v1/predict — the core contract of the service."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

BAD_URL = "http://secure-login-bank.example.com/verify"
GOOD_URL = "https://github.com/faizann24"


def test_predict_returns_fraud_prediction(client: TestClient) -> None:
    response = client.post("/api/v1/predict", json={"url": BAD_URL})
    assert response.status_code == 200

    body = response.json()
    prediction = body["prediction"]

    assert prediction["url"] == BAD_URL
    assert prediction["is_fraud"] is True
    assert prediction["label"] == "bad"
    assert 0.0 <= prediction["probability"] <= 1.0
    assert prediction["model_version"]
    assert body["request_id"]


def test_predict_returns_legitimate_prediction(client: TestClient) -> None:
    response = client.post("/api/v1/predict", json={"url": GOOD_URL})
    assert response.status_code == 200

    prediction = response.json()["prediction"]
    assert prediction["is_fraud"] is False
    assert prediction["label"] == "good"


def test_predict_accepts_bare_hostname(client: TestClient) -> None:
    """The training set stores bare domains, so a missing scheme must work."""
    response = client.post("/api/v1/predict", json={"url": "docs.python.org"})
    assert response.status_code == 200
    # Normalised to an absolute URL before inference.
    assert response.json()["prediction"]["url"] == "http://docs.python.org"


def test_predict_normalises_surrounding_whitespace(client: TestClient) -> None:
    response = client.post("/api/v1/predict", json={"url": f"  {GOOD_URL}  "})
    assert response.status_code == 200
    assert response.json()["prediction"]["url"] == GOOD_URL


def test_threshold_override_changes_decision(client: TestClient) -> None:
    """A very low threshold must flag a URL the model considers safe."""
    strict = client.post("/api/v1/predict", json={"url": GOOD_URL, "threshold": 0.0})
    assert strict.status_code == 200
    assert strict.json()["prediction"]["is_fraud"] is True
    assert strict.json()["prediction"]["threshold"] == 0.0

    lenient = client.post("/api/v1/predict", json={"url": BAD_URL, "threshold": 1.0})
    assert lenient.status_code == 200
    assert lenient.json()["prediction"]["is_fraud"] is False


def test_threshold_is_reported_and_matches_settings(client: TestClient) -> None:
    response = client.post("/api/v1/predict", json={"url": GOOD_URL})
    assert response.json()["prediction"]["threshold"] == 0.5


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-body"),
        pytest.param({"url": ""}, id="empty-url"),
        pytest.param({"url": "   "}, id="blank-url"),
        pytest.param({"url": "ftp://example.com"}, id="bad-scheme"),
        pytest.param({"url": 12345}, id="wrong-type"),
        pytest.param({"url": "https://x.io", "threshold": 1.5}, id="threshold-too-big"),
        pytest.param(
            {"url": "https://x.io", "threshold": -0.1}, id="threshold-negative"
        ),
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, payload: dict) -> None:
    assert client.post("/api/v1/predict", json=payload).status_code == 422


def test_overlong_url_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/predict", json={"url": "http://x.io/" + "a" * 5000})
    assert response.status_code == 422


def test_prediction_is_deterministic(client: TestClient) -> None:
    first = client.post("/api/v1/predict", json={"url": BAD_URL}).json()
    second = client.post("/api/v1/predict", json={"url": BAD_URL}).json()
    assert first["prediction"]["probability"] == second["prediction"]["probability"]


def test_url_is_case_insensitive_for_scheme(client: TestClient) -> None:
    lower = client.post("/api/v1/predict", json={"url": "https://example.com"})
    upper = client.post("/api/v1/predict", json={"url": "HTTPS://example.com"})
    assert lower.status_code == 200
    assert upper.status_code == 200
    assert (
        lower.json()["prediction"]["probability"]
        == upper.json()["prediction"]["probability"]
    )
