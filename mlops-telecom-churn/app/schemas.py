from pydantic import BaseModel, Field
from typing import Optional


class CustomerFeatures(BaseModel):
    gender: str
    SeniorCitizen: int
    Partner: str
    Dependents: str
    tenure: int
    PhoneService: str
    MultipleLines: str
    InternetService: str
    OnlineSecurity: str
    OnlineBackup: str
    DeviceProtection: str
    TechSupport: str
    StreamingTV: str
    StreamingMovies: str
    Contract: str
    PaperlessBilling: str
    PaymentMethod: str
    MonthlyCharges: float
    TotalCharges: float


class PredictionResponse(BaseModel):
    churn_probability: float = Field(..., description="Probability of customer churn")
    prediction: int = Field(..., description="1 = will churn, 0 = will not churn")
    model_name: Optional[str] = None
    model_version: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
