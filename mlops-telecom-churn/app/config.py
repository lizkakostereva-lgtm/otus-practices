from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    model_name: str = Field(default="telecom-churn-model", alias="MODEL_NAME")
    model_stage: str = Field(default="Production", alias="MODEL_STAGE")
    mlflow_tracking_uri: str = Field(default="http://localhost:5000", alias="MLFLOW_TRACKING_URI")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
