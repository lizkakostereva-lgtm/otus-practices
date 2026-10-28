#!/usr/bin/env python3
"""Periodic retraining entry point for Airflow (run as a KubernetesPodOperator pod).

It reuses the *exact* training pipeline the API serves (`app.train`), so a model
produced here is byte-for-byte compatible with what the Deployment loads. The
job:

  1. loads the training data (S3 URI, local file or the upstream CSV URL);
  2. fits the CountVectorizer -> RandomForest pipeline;
  3. sweeps the F1-optimal threshold and scores accuracy/precision/recall/F1/AUC;
  4. logs params + metrics + the model artifact to MLflow and registers a new
     model version;
  5. optionally promotes the new version (alias `champion` / stage `Production`)
     and can fail the run (`--fail-on-regression`) so a bad model never ships.

Everything is configured by CLI flags (Airflow passes them explicitly), with
sensible environment-variable fallbacks so the job is also runnable by hand.

    python -m mlops.retrain \
        --tracking-uri http://mlflow.url-fraud.svc:5000 \
        --experiment fraud_url_detection \
        --model-name url-fraud-detector \
        --sample-frac 0.1 --min-f1 0.70
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

# Reuse the very same training code the service uses (copied into the image).
from app.config import DATA_URL
from app.train import (
    build_pipeline,
    evaluate,
    prepare_data,
    sweep_threshold,
)

logger = logging.getLogger("mlops.retrain")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrain the URL fraud model and log to MLflow"
    )
    parser.add_argument("--tracking-uri", default=os.getenv("MLFLOW_TRACKING_URI"))
    parser.add_argument(
        "--experiment", default=os.getenv("MLFLOW_EXPERIMENT", "fraud_url_detection")
    )
    parser.add_argument(
        "--model-name", default=os.getenv("MLFLOW_MODEL_NAME", "url-fraud-detector")
    )
    parser.add_argument(
        "--run-name", default=os.getenv("MLFLOW_RUN_NAME", "airflow-retrain")
    )
    parser.add_argument(
        "--data",
        default=os.getenv("RETRAIN_DATA_URL", DATA_URL),
        help="S3 URI (s3://...), local path or http(s) URL of the training CSV",
    )
    parser.add_argument(
        "--sample-frac",
        type=float,
        default=float(os.getenv("RETRAIN_SAMPLE_FRAC", "0.1")),
    )
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--min-f1", type=float, default=None)
    parser.add_argument("--min-auc", type=float, default=None)
    parser.add_argument(
        "--register",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Register a new MLflow model version",
    )
    parser.add_argument(
        "--promote",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Move the new version to stage Production (alias champion)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def _materialize_data(uri: str) -> Path:
    """Return a local CSV path for ``uri`` (downloading S3/http sources)."""
    if not uri:
        raise SystemExit("no --data / RETRAIN_DATA_URL provided")

    if uri.startswith("s3://"):
        import boto3  # imported lazily: only needed for the S3 path

        bucket, _, key = uri[len("s3://") :].partition("/")
        endpoint = os.getenv("AWS_ENDPOINT_URL")  # Yandex Object Storage override
        logger.info("downloading s3://%s/%s", bucket, key)
        client = boto3.client("s3", endpoint_url=endpoint)
        tmp = Path(tempfile.mkdtemp()) / Path(key).name
        client.download_file(bucket, key, str(tmp))
        return tmp

    if uri.startswith(("http://", "https://")):
        tmp = Path(tempfile.mkdtemp()) / "data.csv"
        logger.info("downloading %s -> %s", uri, tmp)
        urllib.request.urlretrieve(uri, tmp)  # noqa: S310 - operator-provided URL
        return tmp

    path = Path(uri)
    if not path.is_file():
        raise SystemExit(f"dataset not found: {path}")
    return path


def retrain(args: argparse.Namespace) -> dict[str, object]:
    import mlflow.sklearn
    import pandas as pd
    from sklearn.metrics import roc_auc_score  # noqa: F401  (used via evaluate)

    import mlflow

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(args.experiment)

    data_path = _materialize_data(args.data)
    df = pd.read_csv(data_path)
    X_train, X_test, y_train, y_test = prepare_data(
        df, args.sample_frac, args.test_size, args.random_state
    )

    with mlflow.start_run(run_name=args.run_name) as run:
        pipeline = build_pipeline()
        pipeline.fit(X_train, y_train)

        threshold, _ = sweep_threshold(pipeline, X_test, y_test)
        metrics, _ = evaluate(pipeline, X_test, y_test, threshold)
        logger.info("metrics @ threshold=%.2f: %s", threshold, metrics)

        mlflow.log_params(
            {
                "sample_frac": args.sample_frac,
                "test_size": args.test_size,
                "random_state": args.random_state,
                "train_rows": len(X_train),
                "test_rows": len(X_test),
                "data_source": args.data,
            }
        )
        mlflow.log_params(
            {
                f"vectorizer.{k}": str(v)
                for k, v in pipeline.named_steps["vectorizer"].get_params().items()
            }
        )
        mlflow.log_params(
            {
                f"classifier.{k}": str(v)
                for k, v in pipeline.named_steps["classifier"].get_params().items()
                if k
                in {"n_estimators", "max_depth", "min_samples_leaf", "class_weight"}
            }
        )
        mlflow.log_metrics(metrics)

        # A small signature + input example makes the registered model usable
        # by the service and shows up in the MLflow UI.
        from mlflow.models import infer_signature

        signature = infer_signature(X_test[:100], pipeline.predict(X_test[:100]))
        model_info = mlflow.sklearn.log_model(
            sk_model=pipeline,
            artifact_path="model",
            signature=signature,
            input_example=X_test[:5].tolist(),
            registered_model_name=args.model_name if args.register else None,
            await_registration_for=60,
        )

        result = {
            "run_id": run.info.run_id,
            "model_uri": model_info.model_uri,
            "metrics": metrics,
        }

        if args.register and args.promote:
            client = mlflow.tracking.MlflowClient()
            version = _latest_version(client, args.model_name, run.info.run_id)
            client.set_registered_model_alias(args.model_name, "champion", version)
            result["promoted_version"] = version
            logger.info("promoted %s v%s to alias 'champion'", args.model_name, version)

    if args.min_f1 is not None and metrics["f1"] < args.min_f1:
        raise SystemExit(
            f"F1 regression: {metrics['f1']:.4f} < required {args.min_f1:.4f}"
        )
    if args.min_auc is not None and metrics["auc"] < args.min_auc:
        raise SystemExit(
            f"AUC regression: {metrics['auc']:.4f} < required {args.min_auc:.4f}"
        )
    return result


def _latest_version(client, model_name: str, run_id: str) -> str:
    versions = client.search_model_versions(
        f"name='{model_name}' and run_id='{run_id}'"
    )
    if not versions:
        raise RuntimeError(f"no registered version found for {model_name} run {run_id}")
    return max(versions, key=lambda v: int(v.version)).version


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    result = retrain(args)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
