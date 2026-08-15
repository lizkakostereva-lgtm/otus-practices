#!/usr/bin/env python3
"""Spark job: train fraud detection model and track it with MLflow.

Reads the cleaned Parquet dataset from S3 (output of fraud_cleaning.py),
engineers features, trains LogisticRegression and RandomForest, compares
them by AUC on a holdout set, logs params/metrics/model to the MLflow
Tracking Server and registers the best model in the Model Registry.

Artifacts (serialized model) are stored in S3 (Object Storage) - the
MLflow server is configured with --default-artifact-root s3://...
"""

import argparse
import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.storagelevel import StorageLevel
from pyspark.ml import Pipeline, PipelineModel
from pyspark.ml.classification import LogisticRegression, RandomForestClassifier
from pyspark.ml.evaluation import BinaryClassificationEvaluator, MulticlassClassificationEvaluator
from pyspark.ml.feature import VectorAssembler, StandardScaler


def normalize_dataproc_args(argv):
    """Yandex Data Proc passes all PySpark job args as a single comma-joined
    token (e.g. '--input,s3a://...,--mlflow-tracking-uri,http://...').
    Split it back into separate argv entries before argparse sees it."""
    if len(argv) == 1 and argv[0].startswith("--") and "," in argv[0]:
        return argv[0].split(",")
    return argv


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = normalize_dataproc_args(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="s3a://spark-bucket-ek/fraud_clean_parquet",
                        help="Path to cleaned Parquet dataset")
    parser.add_argument("--mlflow-tracking-uri", required=True,
                        help="MLflow Tracking Server URI, e.g. http://10.x.x.x:5000")
    parser.add_argument("--mlflow-s3-endpoint", default="https://storage.yandexcloud.net",
                        help="S3-compatible endpoint for MLflow artifact upload")
    parser.add_argument("--aws-access-key-id", default=None,
                        help="Static key id (mlflow-sa). Empty -> artifact proxy via server")
    parser.add_argument("--aws-secret-access-key", default=None)
    parser.add_argument("--experiment-name", default="fraud_detection")
    parser.add_argument("--model-name", default="fraud-detector")
    parser.add_argument("--metrics-output",
                        default="s3a://spark-bucket-ek/mlflow_metrics",
                        help="S3 path to append run metrics (CSV)")
    parser.add_argument("--sample-fraction", type=float, default=0.1,
                        help="Fraction of the dataset to train on (0.0-1.0). "
                             "The full 235M-row set is too heavy for a 3-node cluster.")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


MLFLOW_VERSION = "2.16.2"


def ensure_mlflow():
    """Data Proc conda image has no mlflow. Install it on the driver into a
    writable target dir (pip --user fails: job user has no writable HOME).
    Version is pinned to match the Tracking Server (2.16.2): the latest mlflow
    removed `ModelInputExample`, breaking `mlflow.spark` import on the driver."""
    target = "/tmp/mlflow_packages"
    try:
        import mlflow
        if mlflow.__version__ == MLFLOW_VERSION and os.path.abspath(mlflow.__file__).startswith(target):
            return
    except Exception:
        pass
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet",
        "--disable-pip-version-check", "--target", target,
        "--upgrade", f"mlflow=={MLFLOW_VERSION}",
    ])
    sys.path.insert(0, target)


def main():
    args = parse_args()

    # MLflow client env must be set before first mlflow call.
    os.environ["MLFLOW_TRACKING_URI"] = args.mlflow_tracking_uri
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = args.mlflow_s3_endpoint
    os.environ["AWS_DEFAULT_REGION"] = "ru-central1"
    # If no static keys are passed, upload artifacts through the tracking server
    # (server itself has S3 credentials from cloud-init).
    if args.aws_access_key_id:
        os.environ["AWS_ACCESS_KEY_ID"] = args.aws_access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = args.aws_secret_access_key
    else:
        os.environ["MLFLOW_ENABLE_ARTIFACT_PROXY"] = "true"

    ensure_mlflow()
    import mlflow

    spark = SparkSession.builder \
        .appName("FraudTrain") \
        .config("spark.sql.adaptive.enabled", "true") \
        .getOrCreate()

    spark._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
    spark._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
    spark._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")

    df = spark.read.parquet(args.input)
    if 0.0 < args.sample_fraction < 1.0:
        df = df.sample(withReplacement=False, fraction=args.sample_fraction, seed=args.seed)
        print(f"Subsampled dataset with fraction={args.sample_fraction}")
    n_rows = df.count()
    print(f"Loaded {n_rows} rows from {args.input}")
    if n_rows == 0:
        raise SystemExit("Empty training dataset - run the cleaning job first.")

    # ---------- Feature engineering ----------
    cust_w = Window.partitionBy("customer_id")
    term_w = Window.partitionBy("terminal_id")

    feat = (
        df
        .withColumn("avg_customer_amount", F.avg("tx_amount").over(cust_w))
        .withColumn("customer_tx_count", F.count("tx_amount").over(cust_w))
        .withColumn("avg_terminal_amount", F.avg("tx_amount").over(term_w))
        .withColumn("terminal_tx_count", F.count("tx_amount").over(term_w))
        .withColumn(
            "customer_amount_ratio",
            F.col("tx_amount") / F.when(F.col("avg_customer_amount") <= 0, 1.0)
                .otherwise(F.col("avg_customer_amount")),
        )
    )

    feature_cols = [
        "tx_amount", "tx_time_seconds", "tx_time_days",
        "avg_customer_amount", "customer_tx_count",
        "avg_terminal_amount", "terminal_tx_count", "customer_amount_ratio",
    ]

    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features")
    scaler = StandardScaler(inputCol="features", outputCol="scaled_features",
                            withStd=True, withMean=True)

    prep = Pipeline(stages=[assembler, scaler])
    prep_model = prep.fit(feat)
    prepared = prep_model.transform(feat)

    train, test = prepared.randomSplit([0.8, 0.2], seed=args.seed)
    print(f"Train rows: {train.count()}, test rows: {test.count()}")
    # Кэшируем train/test: иначе каждый fit()/count()/transform() заново
    # пересчитывает все shuffle'ы (окна customer/terminal + scaler).
    train.persist(StorageLevel.MEMORY_AND_DISK)
    test.persist(StorageLevel.MEMORY_AND_DISK)

    evaluator = BinaryClassificationEvaluator(labelCol="tx_fraud", rawPredictionCol="rawPrediction")
    pr_evaluator = BinaryClassificationEvaluator(
        labelCol="tx_fraud", rawPredictionCol="rawPrediction", metricName="areaUnderPR")
    mce = MulticlassClassificationEvaluator(labelCol="tx_fraud", predictionCol="prediction")

    # ---------- MLflow run ----------
    mlflow.set_experiment(args.experiment_name)

    with mlflow.start_run() as run:
        run_id = run.info.run_id
        print(f"MLflow run_id: {run_id}")
        all_metrics = {"run_id": run_id}

        mlflow.log_params({
            "n_rows": n_rows,
            "sample_fraction": args.sample_fraction,
            "seed": args.seed,
            "feature_cols": ",".join(feature_cols),
        })

        # 1) LogisticRegression
        lr = LogisticRegression(featuresCol="scaled_features", labelCol="tx_fraud",
                                maxIter=30, regParam=0.01)
        lr_model = lr.fit(train)
        lr_pred = lr_model.transform(test)
        lr_auc = evaluator.evaluate(lr_pred)
        lr_pr_auc = pr_evaluator.evaluate(lr_pred)
        mlflow.log_params({"lr_max_iter": 30, "lr_reg_param": 0.01})
        mlflow.log_metrics({"lr_auc": lr_auc, "lr_pr_auc": lr_pr_auc})
        all_metrics.update({"lr_auc": lr_auc, "lr_pr_auc": lr_pr_auc})
        print(f"LogisticRegression AUC={lr_auc:.4f}, PR-AUC={lr_pr_auc:.4f}")

        # 2) RandomForest
        rf = RandomForestClassifier(featuresCol="scaled_features", labelCol="tx_fraud",
                                    numTrees=30, maxDepth=6, seed=args.seed)
        rf_model = rf.fit(train)
        rf_pred = rf_model.transform(test)
        rf_auc = evaluator.evaluate(rf_pred)
        rf_pr_auc = pr_evaluator.evaluate(rf_pred)
        mlflow.log_params({"rf_num_trees": 30, "rf_max_depth": 6})
        mlflow.log_metrics({"rf_auc": rf_auc, "rf_pr_auc": rf_pr_auc})
        all_metrics.update({"rf_auc": rf_auc, "rf_pr_auc": rf_pr_auc})
        print(f"RandomForest AUC={rf_auc:.4f}, PR-AUC={rf_pr_auc:.4f}")

        # 3) Best model by AUC
        if lr_auc >= rf_auc:
            best_model, best_auc, best_pr_auc, best_name = lr_model, lr_auc, lr_pr_auc, "logistic_regression"
        else:
            best_model, best_auc, best_pr_auc, best_name = rf_model, rf_auc, rf_pr_auc, "random_forest"

        mlflow.set_tag("best_model", best_name)
        mlflow.log_metric("best_auc", best_auc)
        mlflow.log_metric("best_pr_auc", best_pr_auc)
        all_metrics.update({"best_auc": best_auc, "best_pr_auc": best_pr_auc})
        print(f"Best model: {best_name}, AUC={best_auc:.4f}")

        pred = best_model.transform(test)
        # Spark 3.4+ removed unweighted "precision"/"recall" from
        # MulticlassClassificationEvaluator - use the weighted variants.
        precision = mce.setMetricName("weightedPrecision").evaluate(pred)
        recall = mce.setMetricName("weightedRecall").evaluate(pred)
        f1 = mce.setMetricName("f1").evaluate(pred)
        mlflow.log_metrics({"precision": precision, "recall": recall, "f1": f1})
        all_metrics.update({"precision": precision, "recall": recall, "f1": f1})
        test_rows = float(test.count())
        mlflow.log_metric("test_rows", test_rows)
        all_metrics["test_rows"] = test_rows
        all_metrics["best_model"] = best_name

        # Full deployable pipeline: feature engineering -> scaler -> best model.
        full_pipeline = PipelineModel(stages=[assembler, prep_model.stages[1], best_model])

        mlflow.spark.log_model(
            spark_model=full_pipeline,
            artifact_path="best_model",
            registered_model_name=args.model_name,
        )
        print(f"Model registered as '{args.model_name}' (run {run_id})")

    # --- Экспорт метрик в S3 (Object Storage) ---------------------------------
    metrics_df = spark.createDataFrame(
        [(k, str(v)) for k, v in all_metrics.items()],
        ["metric", "value"],
    ).withColumn("run_id", F.lit(run_id))
    metrics_df.coalesce(1) \
        .write.mode("append") \
        .option("header", "true") \
        .csv(args.metrics_output)
    print(f"Metrics exported to {args.metrics_output} (run {run_id})")

    spark.stop()
    print("Training finished.")


if __name__ == "__main__":
    main()
