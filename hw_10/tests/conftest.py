"""Shared pytest fixtures.

Tests must not depend on the 22 MB training CSV: a small deterministic model is
trained once per session into a tmp dir and injected through MODEL_PATH /
MODEL_METADATA_PATH.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline

from app.config import get_settings
from app.main import create_app

# A handful of strings whose labels are stable for a tiny linear-ish model.
TRAIN_URLS = [
    "http://secure-login-bank.example.com/verify",
    "http://account-update-paypal.example.net/login",
    "http://free-prize-winner.example.org/claim",
    "http://confirm-card-details.example.io/auth",
    "http://verify-identity-now.example.co/signin",
    "http://bonus-reward-center.example.dev/get",
    "https://github.com/faizann24",
    "https://docs.python.org/3/library/os.html",
    "https://scikit-learn.org/stable/index.html",
    "https://fastapi.tiangolo.com/",
    "https://www.nytimes.com/",
    "https://stackoverflow.com/questions/123",
]
TRAIN_LABELS = [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0]


@pytest.fixture(scope="session")
def tiny_model_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Train a small pipeline and persist it as a joblib artifact."""
    import joblib

    pipeline = Pipeline(
        steps=[
            (
                "vectorizer",
                CountVectorizer(analyzer="char", ngram_range=(1, 3), min_df=1),
            ),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=25, max_depth=8, random_state=42, n_jobs=1
                ),
            ),
        ]
    )
    pipeline.fit(TRAIN_URLS, TRAIN_LABELS)

    directory = tmp_path_factory.mktemp("model")
    model_path = directory / "model.joblib"
    joblib.dump(pipeline, model_path, compress=3)

    metadata = {
        "model_version": "test-0.0.1",
        "model_type": "sklearn.pipeline.Pipeline",
        "algorithm": "RandomForestClassifier",
        "trained_at": "2026-01-01T00:00:00+00:00",
        "dataset": {"name": "fixture", "train_rows": len(TRAIN_URLS)},
        "sklearn_version": "1.7.1",
        "features": len(pipeline.named_steps["vectorizer"].vocabulary_),
        "metrics": {
            "accuracy": 1.0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "auc": 1.0,
            "threshold": 0.5,
        },
        "params": {"classifier": {"n_estimators": 25}},
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return model_path


@pytest.fixture(scope="session")
def client_settings(tiny_model_path: Path) -> object:
    """Settings pointing at the fixture artifact."""
    os.environ["MODEL_PATH"] = str(tiny_model_path)
    os.environ["MODEL_METADATA_PATH"] = str(tiny_model_path.parent / "metadata.json")
    os.environ["DEFAULT_THRESHOLD"] = "0.5"
    os.environ.pop("TRAIN_ON_STARTUP", None)
    get_settings.cache_clear()
    settings = get_settings()
    yield settings
    for key in ("MODEL_PATH", "MODEL_METADATA_PATH", "DEFAULT_THRESHOLD"):
        os.environ.pop(key, None)
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def client(client_settings) -> Iterator[TestClient]:
    """TestClient with the lifespan executed (model actually loaded)."""
    with TestClient(create_app(client_settings)) as test_client:
        yield test_client


@pytest.fixture
def missing_model_client(client_settings, tmp_path: Path) -> Iterator[TestClient]:
    """Client whose artifact does not exist -> must report not-ready."""
    broken = tmp_path / "does-not-exist.joblib"
    settings = client_settings.__class__(
        model_path=broken,
        metadata_path=tmp_path / "missing.json",
        enable_metrics=False,
        train_on_startup=False,
        version="broken",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
