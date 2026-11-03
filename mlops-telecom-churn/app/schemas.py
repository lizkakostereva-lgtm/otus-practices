
from pydantic import BaseModel, Field


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
    model_name: str | None = None
    model_version: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
