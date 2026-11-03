"""Spark job (full variant): train the churn model on Data Proc and log to MLflow.

Feature engineering mirrors the sklearn variant (src/features/build_features.py):
  - numeric features stay as-is (Spark LR handles scaling internally via
    standardized coefficients; the point of the diploma is automation);
  - categorical features  -> StringIndexer -> OneHotEncoder;
  - assembled into "features" vector -> pyspark.ml LogisticRegression.

The trained pipeline is logged to the MLflow Tracking Server
(mlflow.spark.log_model) and registered in the Model Registry, so the same
promote_model.py threshold logic applies to the Spark variant.
"""

import argparse

import mlflow
import mlflow.spark
from mlflow.models.signature import infer_signature
from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.evaluation import (
    BinaryClassificationEvaluator,
    MulticlassClassificationEvaluator,
)
from pyspark.ml.feature import OneHotEncoder, StringIndexer, VectorAssembler
from pyspark.sql import SparkSession

ALL_CATEGORICAL = [
    "gender", "Partner", "Dependents", "PhoneService", "MultipleLines",
    "InternetService", "OnlineSecurity", "OnlineBackup", "DeviceProtection",
    "TechSupport", "StreamingTV", "StreamingMovies", "Contract",
    "PaperlessBilling", "PaymentMethod",
]
NUMERIC = ["SeniorCitizen", "tenure", "MonthlyCharges", "TotalCharges"]
DROP = ["customerID"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Telco churn: train (Spark ML)")
    parser.add_argument("--input", required=True, help="s3a:// path to cleaned Parquet")
    parser.add_argument("--mlflow-tracking-uri", required=True)
    parser.add_argument("--experiment-name", default="telecom-churn")
    parser.add_argument("--model-name", default="telecom-churn-model")
    parser.add_argument("--reg-param", type=float, default=0.01, help="L2 regularization")
    parser.add_argument("--max-iter", type=int, default=100)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--aws-access-key-id", default=None)
    parser.add_argument("--aws-secret-access-key", default=None)
    args = parser.parse_args()

    spark = SparkSession.builder.appName("churn-train").getOrCreate()
    hadoop = spark._jsc.hadoopConfiguration()  # noqa: SLF001
    hadoop.set("fs.s3a.endpoint", "storage.yandexcloud.net")
    if args.aws_access_key_id and args.aws_secret_access_key:
        hadoop.set("fs.s3a.access.key", args.aws_access_key_id)
        hadoop.set("fs.s3a.secret.key", args.aws_secret_access_key)
        hadoop.set("fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")

    mlflow.set_tracking_uri(args.mlflow_tracking_uri)
    mlflow.set_experiment(args.experiment_name)

    df = spark.read.parquet(args.input).drop(*DROP)
    train_df, test_df = df.randomSplit([1.0 - args.test_size, args.test_size], seed=args.random_state)

    stages = []
    for col in ALL_CATEGORICAL:
        stages.append(StringIndexer(inputCol=col, outputCol=f"{col}_idx", handleInvalid="keep"))
        stages.append(OneHotEncoder(inputCol=f"{col}_idx", outputCol=f"{col}_ohe"))
    stages.append(VectorAssembler(
        inputCols=NUMERIC + [f"{c}_ohe" for c in ALL_CATEGORICAL],
        outputCol="features",
    ))
    lr = LogisticRegression(
        featuresCol="features", labelCol="Churn",
        maxIter=args.max_iter, regParam=args.reg_param,
    )
    stages.append(lr)

    with mlflow.start_run() as run:
        pipeline = Pipeline(stages=stages)
        model = pipeline.fit(train_df)

        preds = model.transform(test_df)

        roc_auc = BinaryClassificationEvaluator(labelCol="Churn").evaluate(preds)
        f1 = MulticlassClassificationEvaluator(labelCol="Churn", metricName="f1").evaluate(preds)
        accuracy = MulticlassClassificationEvaluator(labelCol="Churn", metricName="accuracy").evaluate(preds)

        mlflow.log_params({
            "model": "pyspark_logistic_regression",
            "regParam": args.reg_param,
            "maxIter": args.max_iter,
            "test_size": args.test_size,
            "random_state": args.random_state,
        })
        mlflow.log_metric("roc_auc", roc_auc)
        mlflow.log_metric("f1", f1)
        mlflow.log_metric("accuracy", accuracy)

        sig = infer_signature(df.limit(100).toPandas(), preds.limit(100).select("prediction").toPandas())
        mlflow.spark.log_model(
            model,
            artifact_path="model",
            signature=sig,
            registered_model_name=args.model_name,
        )
        print(f"logged run {run.info.run_id}: ROC-AUC={roc_auc:.4f} F1={f1:.4f} acc={accuracy:.4f}")

    spark.stop()


if __name__ == "__main__":
    main()