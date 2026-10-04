"""Model loading, caching and inference logic.

The estimator itself is a plain scikit-learn ``Pipeline``
(CountVectorizer -> RandomForestClassifier), so there is no framework-specific
wrapper: joblib in, numpy out.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.pipeline import Pipeline

from app.config import DATA_URL, Settings

logger = logging.getLogger(__name__)

LABEL_BAD = "bad"
LABEL_GOOD = "good"

# Guard against pathological inputs reaching the vectorizer.
URL_BATCH_LIMIT = 10_000


class ModelNotAvailableError(RuntimeError):
    """Raised when predictions are requested but no model is loaded."""


@dataclass
class ModelBundle:
    """Estimator plus the metadata written by ``app.train``."""

    pipeline: Pipeline
    version: str
    threshold: float
    metadata: dict[str, Any] = field(default_factory=dict)
    loaded_at: str | None = None


class Predictor:
    """Thread-safe, lazily-initialised holder for the model artifact.

    The model is loaded once per process. ``reload()`` swaps the artifact
    without restarting the pod (used by tests and by future hot-reload work).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bundle: ModelBundle | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._bundle is not None

    @property
    def bundle(self) -> ModelBundle:
        if self._bundle is None:
            raise ModelNotAvailableError(
                "model is not loaded; the service is not ready to serve predictions"
            )
        return self._bundle

    @property
    def version(self) -> str:
        return self.bundle.version if self._bundle else self._settings.version

    def load(self) -> ModelBundle:
        """Load the artifact from disk (idempotent)."""
        with self._lock:
            if self._bundle is not None:
                return self._bundle

            model_path = Path(self._settings.model_path)
            if not model_path.is_file():
                if self._settings.train_on_startup:
                    logger.warning(
                        "model artifact %s not found, training on startup",
                        model_path,
                    )
                    pipeline = _train_fallback_pipeline()
                    version, metadata = self._settings.version, {}
                else:
                    raise FileNotFoundError(
                        f"model artifact not found: {model_path}. "
                        "Run `make train` or set TRAIN_ON_STARTUP=true."
                    )
            else:
                logger.info("loading model from %s", model_path)
                pipeline = joblib.load(model_path)
                version, metadata = _read_metadata(
                    Path(self._settings.metadata_path),
                    fallback_version=self._settings.version,
                )

            self._bundle = ModelBundle(
                pipeline=pipeline,
                version=version,
                threshold=_threshold_from(metadata, self._settings),
                metadata=metadata,
                loaded_at=datetime_now(),
            )
            logger.info(
                "model loaded: version=%s type=%s",
                version,
                type(pipeline).__name__,
            )
            return self._bundle

    def reload(self) -> ModelBundle:
        """Drop the cached bundle and load it again."""
        with self._lock:
            self._bundle = None
        return self.load()

    def predict_proba(self, urls: Sequence[str]) -> np.ndarray:
        """Return the P(class = 'bad') column for ``urls``."""
        bundle = self.bundle
        if not urls:
            return np.empty((0,), dtype=float)
        if len(urls) > URL_BATCH_LIMIT:
            raise ValueError(
                f"too many urls in one call: {len(urls)} > {URL_BATCH_LIMIT}"
            )

        features = np.asarray(urls, dtype=object).reshape(-1)
        try:
            proba = bundle.pipeline.predict_proba(features)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("inference failed")
            raise RuntimeError(f"inference failed: {exc}") from exc

        classes = list(getattr(bundle.pipeline, "classes_", [0, 1]))
        try:
            bad_index = classes.index(1)
        except ValueError as exc:  # pragma: no cover - defensive
            raise RuntimeError(
                f"model was not trained on class 1 ('bad'), classes={classes}"
            ) from exc
        return np.asarray(proba)[:, bad_index]

    def model_info(self) -> dict[str, Any]:
        """Provenance dictionary for GET /api/v1/model/info."""
        bundle = self.bundle
        meta = dict(bundle.metadata)
        vectorizer = _pipeline_step(bundle.pipeline, "vectorizer")
        classifier = _pipeline_step(bundle.pipeline, "classifier")
        pipeline_type = type(bundle.pipeline)

        return {
            "model_version": bundle.version,
            "model_type": (f"{pipeline_type.__module__}.{pipeline_type.__qualname__}"),
            "algorithm": (
                f"{type(classifier).__module__}.{type(classifier).__qualname__}"
                if classifier is not None
                else "unknown"
            ),
            "trained_at": meta.get("trained_at"),
            "artifact_sha256": meta.get("artifact_sha256"),
            "dataset": meta.get("dataset"),
            "sklearn_version": meta.get("sklearn_version", sklearn.__version__),
            "threshold": bundle.threshold,
            "features": (
                len(vectorizer.vocabulary_) if vectorizer is not None else None
            ),
            "metrics": meta.get("metrics", {}),
            "params": meta.get("params", {}),
        }


def _read_metadata(path: Path, fallback_version: str) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        logger.warning("model metadata %s not found, using defaults", path)
        return fallback_version, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("cannot read model metadata from %s", path)
        return fallback_version, {}
    return str(payload.get("model_version", fallback_version)), payload


def _threshold_from(metadata: dict[str, Any], settings: Settings) -> float:
    """Prefer the F1-optimal threshold found at training time.

    Keeping it in metadata.json means retraining automatically moves the
    decision boundary without a code change. An explicit DEFAULT_THRESHOLD
    environment variable always wins.
    """
    if settings.threshold_override is not None:
        return settings.threshold_override
    threshold = metadata.get("metrics", {}).get("threshold")
    if isinstance(threshold, (int, float)) and 0.0 < float(threshold) < 1.0:
        return float(threshold)
    return settings.default_threshold


def _pipeline_step(pipeline: Any, name: str) -> Any:
    steps = getattr(pipeline, "named_steps", None)
    if isinstance(steps, dict):
        return steps.get(name)
    return None


def _train_fallback_pipeline() -> Pipeline:
    """Small fitted model used only when TRAIN_ON_STARTUP=true and the
    artifact is missing. Keeps local runs and demos working; a real
    deployment ships the artifact baked into the image instead.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.pipeline import Pipeline as SkPipeline

    urls = [
        "http://secure-login-bank.example.com/verify",
        "http://account-update-paypal.example.net/login",
        "http://free-prize-winner.example.org/claim",
        "http://confirm-card-details.example.io/auth",
        "https://github.com/faizann24",
        "https://docs.python.org/3/library/os.html",
        "https://scikit-learn.org/stable/index.html",
        "https://fastapi.tiangolo.com/",
    ]
    labels = [1, 1, 1, 1, 0, 0, 0, 0]

    pipeline = SkPipeline(
        steps=[
            (
                "vectorizer",
                CountVectorizer(analyzer="char", ngram_range=(1, 3), min_df=1),
            ),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=20, max_depth=8, random_state=42, n_jobs=1
                ),
            ),
        ]
    )
    pipeline.fit(urls, labels)
    logger.warning(
        "trained a %d-sample fallback model; predictions are meaningless",
        len(urls),
    )
    return pipeline


def datetime_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


__all__ = [
    "LABEL_BAD",
    "LABEL_GOOD",
    "DATA_URL",
    "ModelBundle",
    "ModelNotAvailableError",
    "Predictor",
]
