import os
import yaml
import joblib
import mlflow
import mlflow.sklearn
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, f1_score, accuracy_score

from src.data.load import load_config
from src.features.build_features import build_preprocessor


def main():
    config = load_config()
    df = pd.read_csv(config["data"]["processed_path"])
    target = config["features"]["target"]
    drop_cols = config["features"]["drop_cols"]

    preprocessor, _ = build_preprocessor(df, target, drop_cols)
    X = df.drop(columns=[target] + drop_cols, errors="ignore")
    y = df[target]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=config["data"]["test_size"], random_state=config["data"]["random_state"], stratify=y
    )

    model = LogisticRegression(C=config["train"]["C"], max_iter=config["train"]["max_iter"], class_weight=config["train"]["class_weight"])
    pipe = Pipeline([("preprocessor", preprocessor), ("classifier", model)])

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment(config["mlflow"]["experiment_name"])

    with mlflow.start_run():
        pipe.fit(X_train, y_train)
        y_pred = pipe.predict(X_test)
        y_proba = pipe.predict_proba(X_test)[:, 1]

        roc_auc = roc_auc_score(y_test, y_proba)
        f1 = f1_score(y_test, y_pred)
        acc = accuracy_score(y_test, y_pred)

        mlflow.log_params(config["train"])
        mlflow.log_metric("roc_auc", roc_auc)
        mlflow.log_metric("f1", f1)
        mlflow.log_metric("accuracy", acc)

        os.makedirs("models", exist_ok=True)
        joblib.dump(pipe.named_steps["preprocessor"], "models/preprocessor.joblib")
        joblib.dump(pipe.named_steps["classifier"], "models/model.joblib")
        joblib.dump(pipe, "models/pipeline.joblib")

        mlflow.sklearn.log_model(pipe, "model")
        try:
            run_id = mlflow.active_run().info.run_id
            model_uri = f"runs:/{run_id}/model"
            mlflow.register_model(model_uri, config["mlflow"]["registered_model_name"])
            print("Registered model in Model Registry")
        except Exception as e:
            print(f"Registry registration skipped: {e}")
        print(f"Logged model to MLflow. ROC-AUC={roc_auc:.4f}, F1={f1:.4f}")


if __name__ == "__main__":
    main()
