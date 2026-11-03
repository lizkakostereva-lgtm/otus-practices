import logging

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator

from app.predictor import predictor
from app.schemas import CustomerFeatures, HealthResponse, PredictionResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="MLOps Telecom Churn Prediction API",
    description="Automated MLOps pipeline for telecom customer churn prediction",
    version="1.0.0",
)

Instrumentator().instrument(app).expose(app)


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", model_loaded=predictor.model_loaded)


@app.post("/predict", response_model=PredictionResponse)
def predict(features: CustomerFeatures):
    if not predictor.model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    try:
        result = predictor.predict(features.model_dump())
        return PredictionResponse(**result)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    return {"message": "Telecom Churn Prediction API", "docs": "/docs"}
