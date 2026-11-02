import os
import logging

import joblib

logger = logging.getLogger(__name__)


class ModelPredictor:
    """Loads the model from disk, or from the MLflow Model Registry.

    In Kubernetes the image is generic: the promoted Production model is pulled
    from MLflow at startup, which is what makes "promote -> rollout" possible
    without rebuilding the image.
    """

    def __init__(self):
        self.model = None
        self.preprocessor = None
        self.model_loaded = False
        self.model_source = None
        self._load_model()

    def _load_model(self):
        model_path = os.getenv("MODEL_PATH", "models/model.joblib")
        preprocessor_path = os.getenv("PREPROCESSOR_PATH", "models/preprocessor.joblib")
        try:
            if os.path.exists(model_path):
                self.model = joblib.load(model_path)
                logger.info("Loaded model from %s", model_path)
            if os.path.exists(preprocessor_path):
                self.preprocessor = joblib.load(preprocessor_path)
                logger.info("Loaded preprocessor from %s", preprocessor_path)
            self.model_source = model_path if self.model is not None else None
        except Exception as exc:  # noqa: BLE001 - startup must not crash the pod
            logger.error("Failed to load local model: %s", exc)

        if self.model is None:
            self._load_from_mlflow()

        self.model_loaded = self.model is not None

    def _load_from_mlflow(self):
        tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
        if not tracking_uri:
            logger.info("No local model and MLFLOW_TRACKING_URI is not set; running without a model")
            return
        try:
            import mlflow
            import mlflow.sklearn

            model_name = os.getenv("MODEL_NAME", "telecom-churn-model")
            model_stage = os.getenv("MODEL_STAGE", "Production")
            mlflow.set_tracking_uri(tracking_uri)
            uri = f"models:/{model_name}/{model_stage}"
            logger.info("Local model not found, downloading %s from %s", uri, tracking_uri)
            # The registered pipeline already contains the preprocessor.
            self.model = mlflow.sklearn.load_model(uri)
            self.preprocessor = None
            self.model_source = uri
            logger.info("Loaded model from MLflow registry: %s", uri)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load model from MLflow: %s", exc)

    def predict(self, features_dict):
        if not self.model_loaded or self.model is None:
            raise RuntimeError("Model not loaded")
        import pandas as pd

        df = pd.DataFrame([features_dict])
        if self.preprocessor is not None:
            df = self.preprocessor.transform(df)
        proba = self.model.predict_proba(df)[0][1]
        pred = int(proba >= 0.5)
        return {"churn_probability": float(proba), "prediction": pred}


predictor = ModelPredictor()
