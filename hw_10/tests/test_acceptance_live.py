"""Acceptance tests against a **deployed** instance (public API).

Skipped by default so `pytest` stays hermetic. Enable them against the running
service (NodePort or Load Balancer in Yandex Cloud):

    export API_BASE_URL="http://<node-public-ip>:30080"
    pytest tests/test_acceptance_live.py -v

The same script is available as `scripts/smoke_test.sh` for a quick manual run.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

# Real rows from the training set, so the assertions below are meaningful
# against the production artifact (not the tiny test fixture).
MALICIOUS_URL = "upstreams.info/wp-admin/includes/inst.exe"  # P(bad) = 0.89
LEGITIMATE_URL = "33-montreal.com/history-of-montreal.asp"  # P(bad) = 0.25

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="set API_BASE_URL to run acceptance tests against a live deployment",
)


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        yield client


def test_service_is_reachable(http: httpx.Client) -> None:
    response = http.get("/health")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_readiness_probe_passes(http: httpx.Client) -> None:
    assert http.get("/readyz").status_code == 200


def test_model_info_is_served(http: httpx.Client) -> None:
    response = http.get("/api/v1/model/info")
    assert response.status_code == 200

    body = response.json()
    assert body["model_version"]
    assert body["metrics"]["auc"] > 0.5


def test_predict_over_public_api(http: httpx.Client) -> None:
    response = http.post("/api/v1/predict", json={"url": MALICIOUS_URL})
    assert response.status_code == 200, response.text

    prediction = response.json()["prediction"]
    assert prediction["is_fraud"] is True
    assert prediction["label"] == "bad"
    assert prediction["probability"] > 0.7
    assert 0.0 <= prediction["probability"] <= 1.0


def test_predict_legitimate_url(http: httpx.Client) -> None:
    response = http.post("/api/v1/predict", json={"url": LEGITIMATE_URL})
    assert response.status_code == 200

    prediction = response.json()["prediction"]
    assert prediction["is_fraud"] is False
    assert prediction["label"] == "good"
    assert prediction["probability"] < 0.5


def test_batch_predict_over_public_api(http: httpx.Client) -> None:
    urls = [MALICIOUS_URL, LEGITIMATE_URL, "docs.python.org"]
    response = http.post("/api/v1/predict/batch", json={"urls": urls})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["count"] == 3
    assert len(body["predictions"]) == 3
    assert body["predictions"][0]["is_fraud"] is True
    assert body["predictions"][1]["is_fraud"] is False
    # A bare hostname is normalised before scoring.
    assert body["predictions"][2]["url"] == "http://docs.python.org"


def test_openapi_docs_over_public_api(http: httpx.Client) -> None:
    assert http.get("/openapi.json").status_code == 200
    assert http.get("/docs").status_code == 200


def test_bad_request_over_public_api(http: httpx.Client) -> None:
    assert http.post("/api/v1/predict", json={}).status_code == 422


def test_prometheus_metrics_over_public_api(http: httpx.Client) -> None:
    response = http.get("/metrics")
    assert response.status_code == 200
    assert "url_fraud_predictions_total" in response.text
