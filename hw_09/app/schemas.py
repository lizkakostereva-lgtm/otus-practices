"""Pydantic request/response models — the public API contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings

Label = Literal["bad", "good"]

# "example.com/path" -> "http://example.com/path".
# The training set stores bare hostnames, so a missing scheme must not be an error.
_ALLOWED_SCHEMES = ("http", "https")


def normalize_url(raw: str) -> str:
    """Trim, add a scheme when missing and reject obviously broken input."""
    value = raw.strip()
    if not value:
        raise ValueError("url must not be empty")
    if "://" not in value:
        value = f"http://{value}"

    scheme, _, remainder = value.partition("://")
    if scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("url scheme must be http or https")

    # The authority is what the classifier actually looks at; a host with
    # spaces or an empty one means the caller sent us prose, not a URL.
    authority = remainder.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    authority = authority.rsplit("@", 1)[-1]  # strip user:pass@
    if not authority:
        raise ValueError("url must contain a host")
    if any(char.isspace() for char in authority):
        raise ValueError("url host must not contain whitespace")
    # The only colon allowed in an authority is a numeric port. This is what
    # separates "example.com:8080" from a scheme-less "javascript:alert(1)".
    host, sep, port = authority.rpartition(":")
    if sep and (not port.isdigit() or not host):
        raise ValueError("url must contain a host and an optional numeric port")
    return value


class PredictRequest(BaseModel):
    """Single-URL prediction request."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"url": "http://secure-banking-login.example.com"}
        }
    )

    url: Annotated[str, Field(min_length=1, description="URL or bare hostname")]
    threshold: Annotated[
        float | None,
        Field(
            default=None,
            ge=0.0,
            le=1.0,
            description="Decision threshold; defaults to DEFAULT_THRESHOLD",
        ),
    ] = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        settings = get_settings()
        normalized = normalize_url(value)
        if len(normalized) > settings.max_url_length:
            raise ValueError(f"url is longer than {settings.max_url_length} characters")
        return normalized


class BatchPredictRequest(BaseModel):
    """Batch prediction request."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "urls": [
                    "http://secure-banking-login.example.com",
                    "https://github.com/faizann24",
                ]
            }
        }
    )

    urls: Annotated[
        list[str], Field(min_length=1, description="List of URLs or hostnames")
    ]
    threshold: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)] = None

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, value: list[str]) -> list[str]:
        settings = get_settings()
        if len(value) > settings.max_batch_size:
            raise ValueError(
                f"batch is limited to {settings.max_batch_size} urls, got {len(value)}"
            )
        normalized = [normalize_url(item) for item in value]
        if any(len(item) > settings.max_url_length for item in normalized):
            raise ValueError(
                f"every url must be at most {settings.max_url_length} characters"
            )
        return normalized


class Prediction(BaseModel):
    """Prediction for a single URL."""

    url: str = Field(description="Normalized URL that was scored")
    label: Label = Field(description="Raw model class: 'bad' or 'good'")
    is_fraud: bool = Field(description="True when probability >= threshold")
    probability: float = Field(ge=0.0, le=1.0, description="P(class = 'bad')")
    threshold: float = Field(ge=0.0, le=1.0, description="Threshold used")
    model_version: str = Field(description="Version of the served model artifact")


class PredictResponse(BaseModel):
    """Response of POST /api/v1/predict."""

    prediction: Prediction
    request_id: str = Field(description="Correlation id, also in X-Request-ID header")


class BatchPredictResponse(BaseModel):
    """Response of POST /api/v1/predict/batch."""

    predictions: list[Prediction]
    count: int = Field(ge=0)
    request_id: str


class ModelInfo(BaseModel):
    """Model provenance exposed by GET /api/v1/model/info."""

    model_version: str
    model_type: str
    algorithm: str
    trained_at: str | None = None
    artifact_sha256: str | None = None
    dataset: dict[str, object] | str | None = None
    sklearn_version: str | None = None
    threshold: float
    features: int | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Liveness / readiness payload."""

    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    model_loaded: bool


class ErrorResponse(BaseModel):
    """Uniform error body for non-2xx responses."""

    error: str
    detail: str
    request_id: str | None = None


class ServiceInfo(BaseModel):
    """Payload of GET / — handy for humans and smoke tests."""

    service: str
    version: str
    environment: str
    description: str
    docs: str
    endpoints: dict[str, str]
