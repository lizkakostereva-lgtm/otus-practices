"""Unit tests for URL normalisation and the Predictor wrapper."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.predictor import ModelNotAvailableError, Predictor
from app.schemas import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "http://example.com"),
        ("  example.com  ", "http://example.com"),
        ("http://example.com", "http://example.com"),
        ("https://example.com/path?q=1", "https://example.com/path?q=1"),
        ("HTTPS://Example.com", "HTTPS://Example.com"),
        ("example.com:8080/x", "http://example.com:8080/x"),
    ],
)
def test_normalize_url_adds_scheme(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ftp://example.com", "javascript:alert(1)", "http://"],
)
def test_normalize_url_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(raw)


def test_predictor_raises_before_load(tmp_path: Path) -> None:
    settings = Settings(
        model_path=tmp_path / "nope.joblib",
        metadata_path=tmp_path / "nope.json",
        train_on_startup=False,
    )
    predictor = Predictor(settings)
    assert predictor.is_loaded is False

    with pytest.raises(ModelNotAvailableError):
        _ = predictor.bundle
    with pytest.raises(ModelNotAvailableError):
        predictor.predict_proba(["https://example.com"])


def test_predictor_load_is_idempotent(tiny_model_path: Path) -> None:
    settings = Settings(
        model_path=tiny_model_path,
        metadata_path=tiny_model_path.parent / "metadata.json",
        threshold_override=0.5,
    )
    predictor = Predictor(settings)
    first = predictor.load()
    second = predictor.load()
    assert first is second
    assert predictor.is_loaded
    assert predictor.version == "test-0.0.1"


def test_predict_proba_returns_bad_class_column(tiny_model_path: Path) -> None:
    settings = Settings(
        model_path=tiny_model_path,
        metadata_path=tiny_model_path.parent / "metadata.json",
        threshold_override=0.5,
    )
    predictor = Predictor(settings)
    predictor.load()

    proba = predictor.predict_proba(
        [
            "http://secure-login-bank.example.com/verify",
            "https://github.com/faizann24",
        ]
    )
    assert isinstance(proba, np.ndarray)
    assert proba.shape == (2,)
    assert ((proba >= 0) & (proba <= 1)).all()


def test_predict_proba_on_empty_list(tiny_model_path: Path) -> None:
    settings = Settings(
        model_path=tiny_model_path,
        metadata_path=tiny_model_path.parent / "metadata.json",
        threshold_override=0.5,
    )
    predictor = Predictor(settings)
    predictor.load()
    assert predictor.predict_proba([]).shape == (0,)


def test_threshold_comes_from_metadata(tiny_model_path: Path) -> None:
    settings = Settings(
        model_path=tiny_model_path,
        metadata_path=tiny_model_path.parent / "metadata.json",
        threshold_override=0.5,
    )
    assert Predictor(settings).load().threshold == 0.5


def test_reload_picks_up_new_artifact(tiny_model_path: Path) -> None:
    settings = Settings(
        model_path=tiny_model_path,
        metadata_path=tiny_model_path.parent / "metadata.json",
        threshold_override=0.5,
    )
    predictor = Predictor(settings)
    predictor.load()
    assert predictor.reload().pipeline is not None
    assert predictor.is_loaded


def test_real_committed_model_artifact_loads() -> None:
    """The artifact baked into the Docker image must be loadable."""
    from app.config import BASE_DIR

    model_path = BASE_DIR / "models" / "model.joblib"
    if not model_path.is_file():
        pytest.skip("models/model.joblib is not present (run `make train`)")

    settings = Settings(model_path=model_path, train_on_startup=False)
    predictor = Predictor(settings)
    predictor.load()

    proba = predictor.predict_proba(["http://secure-login-bank.example.com"])
    assert 0.0 <= float(proba[0]) <= 1.0


def test_train_on_startup_builds_a_usable_model(tmp_path: Path) -> None:
    settings = Settings(
        model_path=tmp_path / "absent.joblib",
        metadata_path=tmp_path / "absent.json",
        train_on_startup=True,
    )
    predictor = Predictor(settings)
    predictor.load()
    assert predictor.is_loaded
    assert predictor.predict_proba(["https://example.com"]).shape == (1,)


def test_missing_artifact_without_flag_raises(tmp_path: Path) -> None:
    settings = Settings(
        model_path=tmp_path / "absent.joblib",
        metadata_path=tmp_path / "absent.json",
        train_on_startup=False,
    )
    with pytest.raises(FileNotFoundError):
        Predictor(settings).load()


def test_settings_default_threshold_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DEFAULT_THRESHOLD", "0.77")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.threshold_override == 0.77
        assert settings.default_threshold == 0.77
    finally:
        monkeypatch.delenv("DEFAULT_THRESHOLD", raising=False)
        get_settings.cache_clear()
    assert os.getenv("DEFAULT_THRESHOLD") is None
