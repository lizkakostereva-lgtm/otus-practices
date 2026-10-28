"""Runtime configuration, read from environment variables (12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Training data is only needed by `python -m app.train`, never by the API.
DATA_URL = (
    "https://raw.githubusercontent.com/faizann24/"
    "Using-machine-learning-to-detect-malicious-URLs/master/data/data.csv"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    service_name: str = field(
        default_factory=lambda: os.getenv("SERVICE_NAME", "url-fraud-api")
    )
    version: str = field(default_factory=lambda: os.getenv("SERVICE_VERSION", "1.0.0"))
    environment: str = field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "production")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    model_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("MODEL_PATH", str(BASE_DIR / "models" / "model.joblib"))
        )
    )
    metadata_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("MODEL_METADATA_PATH", str(BASE_DIR / "models" / "metadata.json"))
        )
    )

    # Train a throwaway model at startup if the artifact is missing.
    # Handy for local runs; keep it OFF in the image so a corrupt artifact
    # fails loudly instead of silently serving an untrained model.
    train_on_startup: bool = field(
        default_factory=lambda: _env_bool("TRAIN_ON_STARTUP", False)
    )

    default_threshold: float = field(
        default_factory=lambda: _env_float("DEFAULT_THRESHOLD", 0.5)
    )
    # Set when the operator pins DEFAULT_THRESHOLD, which then wins over the
    # F1-optimal threshold stored in the model metadata.
    threshold_override: float | None = field(
        default_factory=lambda: (
            _env_float("DEFAULT_THRESHOLD", 0.5)
            if "DEFAULT_THRESHOLD" in os.environ
            else None
        )
    )
    max_url_length: int = field(
        default_factory=lambda: _env_int("MAX_URL_LENGTH", 2048)
    )
    max_batch_size: int = field(default_factory=lambda: _env_int("MAX_BATCH_SIZE", 100))

    enable_metrics: bool = field(
        default_factory=lambda: _env_bool("ENABLE_METRICS", True)
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list("CORS_ORIGINS", ["*"])
    )

    def as_public_dict(self) -> dict[str, object]:
        """Settings safe to expose over HTTP (no secrets, no absolute paths)."""
        return {
            "service_name": self.service_name,
            "version": self.version,
            "environment": self.environment,
            "default_threshold": self.default_threshold,
            "max_batch_size": self.max_batch_size,
            "max_url_length": self.max_url_length,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (use as a FastAPI dependency)."""
    return Settings()
