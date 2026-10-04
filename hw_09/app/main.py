"""FastAPI application: REST interface for the malicious-URL classifier.

Endpoints
    GET  /                       service banner
    GET  /health                 liveness  (k8s livenessProbe)
    GET  /healthz                liveness alias
    GET  /readyz                 readiness (k8s readinessProbe, 503 until model loads)
    POST /api/v1/predict         predict one URL
    POST /api/v1/predict/batch   predict many URLs
    GET  /api/v1/model/info      model provenance + training metrics
    GET  /metrics                Prometheus exposition (optional)
    GET  /docs                   OpenAPI UI
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.predictor import (
    LABEL_BAD,
    LABEL_GOOD,
    ModelNotAvailableError,
    Predictor,
)
from app.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    ErrorResponse,
    HealthResponse,
    ModelInfo,
    Prediction,
    PredictRequest,
    PredictResponse,
    ServiceInfo,
)

logger = logging.getLogger("app")

DESCRIPTION = """
REST API around a scikit-learn **malicious-URL classifier**
(`CountVectorizer(char 1-3 grams)` -> `RandomForestClassifier`).

`1` = `bad` (phishing / malicious), `0` = `good` (legitimate).
Requests accept a full URL or a bare hostname, with or without a scheme.
""".strip()

# Prometheus counters, shared by every app instance in the process.
try:
    from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE_LATEST
    from prometheus_client import Counter as _Counter
    from prometheus_client import generate_latest as _generate_latest

    _PREDICTIONS_TOTAL = _Counter(
        "url_fraud_predictions_total",
        "Number of URLs scored, labelled by the returned class",
        ["result"],
    )
    _BATCHES_TOTAL = _Counter(
        "url_fraud_batches_total", "Number of batch prediction requests served"
    )
except ImportError:  # pragma: no cover - metrics are optional
    _CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    _generate_latest = None  # type: ignore[assignment]
    _PREDICTIONS_TOTAL = None  # type: ignore[assignment]
    _BATCHES_TOTAL = None  # type: ignore[assignment]


def get_predictor(request: Request) -> Predictor:
    """FastAPI dependency exposing the process-wide predictor.

    The instance lives on ``app.state`` rather than in a module global so that
    several app objects (tests, workers) never overwrite each other.
    """
    predictor = getattr(request.app.state, "predictor", None)
    if predictor is None:  # pragma: no cover - guarded by lifespan
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="predictor is not initialised",
        )
    return predictor


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"message":"%(message)s"}',
    )


def _describe(
    url: str, probability: float, threshold: float, version: str
) -> Prediction:
    is_fraud = probability >= threshold
    return Prediction(
        url=url,
        label=LABEL_BAD if is_fraud else LABEL_GOOD,
        is_fraud=is_fraud,
        probability=round(float(probability), 6),
        threshold=threshold,
        model_version=version,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the model once at startup and release it on shutdown."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info("starting %s v%s", settings.service_name, settings.version)

    predictor = Predictor(settings)
    app.state.predictor = predictor
    try:
        predictor.load()
    except Exception:
        # Do not crash the process: /readyz reports "not ready" so that the
        # orchestrator can hold traffic instead of restart-looping.
        logger.exception("model failed to load, service will report not-ready")

    yield
    logger.info("shutting down %s", settings.service_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.service_name,
        version=settings.version,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.started_at = time.time()

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Attach a request id and one structured log line per request."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={"request_id": request_id, "path": request.url.path},
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.exception_handler(ModelNotAvailableError)
    async def model_unavailable_handler(
        request: Request, exc: ModelNotAvailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error="model_not_available",
                detail=str(exc),
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )

    # ---------------------------------------------------------------- meta ---
    @app.get("/", response_model=ServiceInfo, tags=["meta"])
    def root(settings: Settings = Depends(get_settings)) -> ServiceInfo:
        return ServiceInfo(
            service=settings.service_name,
            version=settings.version,
            environment=settings.environment,
            description="REST API for the malicious-URL classifier",
            docs="/docs",
            endpoints={
                "predict": "POST /api/v1/predict",
                "predict_batch": "POST /api/v1/predict/batch",
                "model_info": "GET /api/v1/model/info",
                "health": "GET /health",
                "ready": "GET /readyz",
                "metrics": "GET /metrics",
            },
        )

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["health"],
        summary="Liveness probe",
    )
    def health(
        response: Response,
        predictor: Predictor = Depends(get_predictor),
        settings: Settings = Depends(get_settings),
    ) -> HealthResponse:
        loaded = predictor.is_loaded
        if not loaded:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if loaded else "degraded",
            service=settings.service_name,
            version=settings.version,
            environment=settings.environment,
            model_loaded=loaded,
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/readyz",
        response_model=HealthResponse,
        tags=["health"],
        summary="Readiness probe (503 until the model is loaded)",
    )
    def readyz(
        response: Response,
        predictor: Predictor = Depends(get_predictor),
        settings: Settings = Depends(get_settings),
    ) -> HealthResponse:
        loaded = predictor.is_loaded
        if not loaded:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if loaded else "degraded",
            service=settings.service_name,
            version=settings.version,
            environment=settings.environment,
            model_loaded=loaded,
        )

    @app.get(
        "/api/v1/model/info",
        response_model=ModelInfo,
        tags=["model"],
        summary="Model provenance and training metrics",
    )
    def model_info(predictor: Predictor = Depends(get_predictor)) -> ModelInfo:
        return ModelInfo(**predictor.model_info())

    # ------------------------------------------------------------- predict ---
    @app.post(
        "/api/v1/predict",
        response_model=PredictResponse,
        tags=["predict"],
        summary="Predict whether a single URL is malicious",
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request"},
            503: {"model": ErrorResponse, "description": "Model unavailable"},
        },
    )
    def predict(
        payload: PredictRequest,
        request: Request,
        predictor: Predictor = Depends(get_predictor),
        settings: Settings = Depends(get_settings),
    ) -> PredictResponse:
        threshold = (
            payload.threshold
            if payload.threshold is not None
            else predictor.bundle.threshold
        )
        probability = float(predictor.predict_proba([payload.url])[0])
        prediction = _describe(payload.url, probability, threshold, predictor.version)
        if _PREDICTIONS_TOTAL is not None:
            _PREDICTIONS_TOTAL.labels(result=prediction.label).inc()
        return PredictResponse(
            prediction=prediction,
            request_id=getattr(request.state, "request_id", ""),
        )

    @app.post(
        "/api/v1/predict/batch",
        response_model=BatchPredictResponse,
        tags=["predict"],
        summary="Predict a batch of URLs in one call",
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request"},
            503: {"model": ErrorResponse, "description": "Model unavailable"},
        },
    )
    def predict_batch(
        payload: BatchPredictRequest,
        request: Request,
        predictor: Predictor = Depends(get_predictor),
        settings: Settings = Depends(get_settings),
    ) -> BatchPredictResponse:
        threshold = (
            payload.threshold
            if payload.threshold is not None
            else predictor.bundle.threshold
        )
        probabilities = predictor.predict_proba(payload.urls)
        predictions = [
            _describe(url, float(prob), threshold, predictor.version)
            for url, prob in zip(payload.urls, probabilities, strict=True)
        ]
        if _PREDICTIONS_TOTAL is not None:
            for prediction in predictions:
                _PREDICTIONS_TOTAL.labels(result=prediction.label).inc()
            _BATCHES_TOTAL.inc()
        return BatchPredictResponse(
            predictions=predictions,
            count=len(predictions),
            request_id=getattr(request.state, "request_id", ""),
        )

    # ------------------------------------------------------------- metrics ---
    if settings.enable_metrics and _PREDICTIONS_TOTAL is not None:

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            return Response(
                content=_generate_latest(),
                media_type=_CONTENT_TYPE_LATEST,
            )

    return app


app = create_app()
