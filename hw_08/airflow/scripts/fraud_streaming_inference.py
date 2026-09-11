#!/usr/bin/env python3
"""Spark Structured Streaming job: online fraud inference.

Роль в hw_08: online-инференс модели из MLflow Model Registry на потоке
транзакций из Kafka.

Cхема данных:
    ... producer (kafka_producer.py) -> Kafka topic "fraud-transactions"
    -> этот job:
        * читает поток из Kafka (spark-sql-kafka),
        * распарсивает JSON-сообщение в признаки,
        * применяет сохранённую PipelineModel (VectorAssembler + StandardScaler
          + RandomForest), зарегистрированную в MLflow как 'fraud-detector'
          (champion = stage Production),
        * пишет предсказания (transaction_id, prediction, fraud_probability)
          обратно в Kafka topic "fraud-predictions",
        * по завершении (--duration-seconds) логирует сводные метрики
          (consumed, latency, input/processed rate) в MLflow и CSV в S3.

Выбор версии модели: --model-uri, по умолчанию models:/fraud-detector/Production
(= champion из hw_07). Registry работает через MLflow Tracking Server на
mlflow-vm; артефакты модели скачиваются из S3 через mlflow-sa (aWстраницы в env,
те же, что использует fraud_train.py / fraud_ab_test.py).

Замечание про checkpoint: streaming-query обязана иметь checkpointLocation
(уникальный на запуск, иначе state при повторном старте «поедет»). DAG передаёт
s3a://<bucket>/stream_checkpoint/<run_id>.
"""

import argparse
import os
import sys
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.ml.functions import vector_to_array
from pyspark.sql.types import (
    StructType, StructField, LongType, DoubleType, IntegerType, StringType,
)

MLFLOW_VERSION = "2.16.2"

FEATURE_COLS = [
    "tx_amount", "tx_time_seconds", "tx_time_days",
    "avg_customer_amount", "customer_tx_count",
    "avg_terminal_amount", "terminal_tx_count", "customer_amount_ratio",
]

MESSAGE_SCHEMA = StructType([
    StructField("transaction_id", LongType()),
    StructField("tx_datetime", StringType()),
    StructField("customer_id", LongType()),
    StructField("terminal_id", LongType()),
    StructField("tx_amount", DoubleType()),
    StructField("tx_time_seconds", LongType()),
    StructField("tx_time_days", IntegerType()),
    StructField("avg_customer_amount", DoubleType()),
    StructField("customer_tx_count", LongType()),
    StructField("avg_terminal_amount", DoubleType()),
    StructField("terminal_tx_count", LongType()),
    StructField("customer_amount_ratio", DoubleType()),
    StructField("tx_fraud", IntegerType()),
    StructField("ts_ms", LongType()),
])


def normalize_dataproc_args(argv):
    if len(argv) == 1 and argv[0].startswith("--") and "," in argv[0]:
        return argv[0].split(",")
    return argv


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = normalize_dataproc_args(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-servers", required=True,
                        help="Kafka bootstrap-servers (SASL_PLAINTEXT, port 9092)")
    parser.add_argument("--input-topic", default="fraud-transactions")
    parser.add_argument("--output-topic", default="fraud-predictions")
    parser.add_argument("--user", default="fraud-user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--model-uri", default="models:/fraud-detector/Production",
                        help="MLflow Model Registry URI (champion = Production)")
    parser.add_argument("--mlflow-tracking-uri", required=True)
    parser.add_argument("--mlflow-s3-endpoint", default="https://storage.yandexcloud.net")
    parser.add_argument("--aws-access-key-id", default=None)
    parser.add_argument("--aws-secret-access-key", default=None)
    parser.add_argument("--checkpoint-location", required=True,
                        help="Unique S3 path for the streaming query checkpoint")
    parser.add_argument("--duration-seconds", type=int, default=120,
                        help="How long to run the streaming query")
    parser.add_argument("--processing-time", default="2 seconds",
                        help="Structured streaming trigger interval")
    parser.add_argument("--run-id", default=None,
                        help="Shared run id (DAG); generated if empty")
    parser.add_argument("--metrics-output",
                        default="s3a://spark-bucket-ek/kafka_test_metrics")
    parser.add_argument("--experiment-name", default="fraud_streaming")
    return parser.parse_args(argv)


def ensure_mlflow():
    """Гарантирует, что загружен тот же mlflow, что и Tracking Server
    (см. аналогичную функцию в fraud_train.py)."""
    import mlflow
    import mlflow.models as _models

    for _name in ("ModelInputExample", "ModelSignature"):
        if not hasattr(_models, _name):
            try:
                setattr(_models, _name, _models.Model)
                print(f"Shimmed mlflow.models.{_name} (was swallowed)", flush=True)
            except Exception as _exc:
                print(f"Could not shim mlflow.models.{_name}: {_exc}", flush=True)

    import mlflow.spark  # noqa: F401 - fail fast if spark logging would break
    path = os.path.abspath(mlflow.__file__)
    print(f"Using mlflow {mlflow.__version__} from {path}", flush=True)
    if mlflow.__version__ != MLFLOW_VERSION:
        raise RuntimeError(f"Expected mlflow {MLFLOW_VERSION}, got "
                           f"{mlflow.__version__} loaded from {path}")


def _kafka_opts(args):
    """Общие SASL_SSL/SCRAM-SHA-512 опции для источника и приёмника Kafka."""
    return {
        "kafka.bootstrap.servers": args.bootstrap_servers,
        "kafka.security.protocol": "SASL_PLAINTEXT",
        "kafka.sasl.mechanism": "SCRAM-SHA-512",
        "kafka.sasl.jaas.config": (
            'org.apache.kafka.common.security.scram.ScramLoginModule required '
            f'username="{args.user}" password="{args.password}";'
        ),
    }


def main():
    args = parse_args()

    os.environ["MLFLOW_TRACKING_URI"] = args.mlflow_tracking_uri
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = args.mlflow_s3_endpoint
    os.environ["AWS_DEFAULT_REGION"] = "ru-central1"
    if args.aws_access_key_id:
        os.environ["AWS_ACCESS_KEY_ID"] = args.aws_access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = args.aws_secret_access_key
    else:
        os.environ["MLFLOW_ENABLE_ARTIFACT_PROXY"] = "true"

    ensure_mlflow()
    import mlflow
    import mlflow.spark

    spark = (SparkSession.builder
             .appName("FraudStreamingInference")
             .config("spark.sql.adaptive.enabled", "true")
             .getOrCreate())
    spark._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
    spark._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
    spark._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")

    run_id = args.run_id or f"run-{int(time.time()*1000)}"

    # Checkpoint должен быть УНИКАЛЕН на запуск (иначе повторный старт с тем же
    # checkpointLocation «поедет» и упадёт). DAG передаёт только БАЗОВЫЙ путь,
    # сюда дописываем run_id — так безопасно запускать DAG много раз подряд.
    args.checkpoint_location = f"{args.checkpoint_location.strip('/')}/{run_id}"
    print(f"Checkpoint location: {args.checkpoint_location}", flush=True)

    # ---------- Загрузка champion-модели из MLflow Registry ----------
    print(f"Loading model from {args.model_uri} ...", flush=True)
    model = mlflow.spark.load_model(args.model_uri)
    print(f"Model loaded: {args.model_uri}", flush=True)

    # ---------- Источник: Kafka ----------
    kafka_opts = _kafka_opts(args)
    raw = (spark.readStream
           .format("kafka")
           .options(**kafka_opts)
           .option("subscribe", args.input_topic)
           .option("startingOffsets", "earliest")
           .option("failOnDataLoss", "false")
           .option("maxOffsetsPerTrigger", "100000")
           .load())

    stream = (
        raw
        .selectExpr("CAST(value AS STRING) AS body")
        .select(F.from_json(F.col("body"), MESSAGE_SCHEMA).alias("d"))
        .select("d.*")
    )

    # ---------- Сводные статистики (аккумулируются между батчами) ----------
    stats = {"consumed": 0, "total_latency_ms": 0.0, "max_latency_ms": 0.0,
             "min_latency_ms": None, "batches": 0}
    # Матрица ошибок для оценки качества модели прямо в потоке:
    # prediction из модели против эталонной метки tx_fraud из сообщения.
    cm = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


    def _quality_metrics():
        """Считает accuracy/precision/recall/f1 по накопленной матрице ошибок."""
        tp, fp, tn, fn = cm["tp"], cm["fp"], cm["tn"], cm["fn"]
        n = tp + fp + tn + fn
        if n == 0:
            return {"n_labels": 0, "accuracy": 0.0, "precision": 0.0,
                    "recall": 0.0, "f1": 0.0}
        acc = (tp + tn) / n
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        return {"n_labels": n, "accuracy": round(acc, 4),
                "precision": round(prec, 4), "recall": round(rec, 4),
                "f1": round(f1, 4)}


    def write_batch(micro_df, batch_id):
        """Применяет модель к микро-батчу и пишет предсказания в Kafka."""
        start = time.time()
        pred = model.transform(
            micro_df.select(*FEATURE_COLS, "transaction_id", "ts_ms", "tx_fraud")
        )
        out = pred.select(
            F.struct(
                F.col("transaction_id").cast("long").alias("transaction_id"),
                F.col("prediction").cast("long").alias("prediction"),
                F.round(vector_to_array(F.col("probability"))[1].cast("double"), 6)
                    .alias("fraud_probability"),
                F.col("ts_ms").cast("long").alias("event_ms"),
            ).alias("message"),
        ).select(F.to_json(F.col("message")).alias("value"))

        out.write \
            .format("kafka") \
            .options(**kafka_opts) \
            .option("topic", args.output_topic) \
            .save()

        # ---- Оценка качества: сверяем предсказание с эталонной меткой ----
        pred_pairs = pred.select(
            F.col("prediction").cast("long").alias("prediction"),
            F.col("tx_fraud").cast("long").alias("label"),
        ).collect()
        for p in pred_pairs:
            pl, lb = int(p["prediction"]), int(p["label"])
            if pl == 1 and lb == 1:
                cm["tp"] += 1
            elif pl == 1 and lb == 0:
                cm["fp"] += 1
            elif pl == 0 and lb == 0:
                cm["tn"] += 1
            else:
                cm["fn"] += 1

        n_rows = micro_df.count()
        batch_ms = (time.time() - start) * 1000
        stats["consumed"] += n_rows
        stats["batches"] += 1
        stats["total_latency_ms"] += batch_ms
        stats["max_latency_ms"] = max(stats["max_latency_ms"], batch_ms)
        print(f"[batch {batch_id}] rows={n_rows} batch_ms={batch_ms:.1f}", flush=True)

    query = (stream.writeStream
             .foreachBatch(write_batch)
             .outputMode("append")
             .trigger(processingTime=args.processing_time)
             .option("checkpointLocation", args.checkpoint_location.strip("/"))
             .start())

    print(f"Streaming started for {args.duration_seconds}s (run {run_id})", flush=True)
    deadline = time.time() + max(args.duration_seconds, 1)
    while query.isActive and time.time() < deadline:
        time.sleep(5)
    if query.isActive:
        print("Time budget exceeded - stopping query", flush=True)
        query.stop()
    query.awaitTermination(60)

    last_progress = query.lastProgress or {}
    input_rate = last_progress.get("inputRowsPerSecond") or 0.0
    processed_rate = last_progress.get("processedRowsPerSecond") or 0.0
    elapsed = args.duration_seconds
    avg_batch_ms = (stats["total_latency_ms"] / stats["batches"]) if stats["batches"] else 0.0
    quality = _quality_metrics()

    print(f"SUMMARY consumed={stats['consumed']} batches={stats['batches']} "
          f"avg_batch_ms={avg_batch_ms:.1f} input_rate={input_rate:.1f}/s "
          f"processed_rate={processed_rate:.1f}/s "
          f"| quality acc={quality['accuracy']} prec={quality['precision']} "
          f"rec={quality['recall']} f1={quality['f1']}", flush=True)

    # ---------- Логируем сводку в MLflow ----------
    mlflow.set_experiment(args.experiment_name)
    with mlflow.start_run() as run:
        mlflow.set_tag("dag_run_id", run_id)
        mlflow.log_param("model_uri", args.model_uri)
        mlflow.log_param("run_id", run_id)
        mlflow.log_metrics({
            "consumed": float(stats["consumed"]),
            "batches": float(stats["batches"]),
            "avg_batch_processing_ms": round(avg_batch_ms, 3),
            "max_batch_processing_ms": round(stats["max_latency_ms"], 3),
            "input_rows_per_second": round(input_rate, 3),
            "processed_rows_per_second": round(processed_rate, 3),
            # качество модели, оцененное на потоковых предсказаниях
            "accuracy": float(quality["accuracy"]),
            "precision": float(quality["precision"]),
            "recall": float(quality["recall"]),
            "f1": float(quality["f1"]),
        })
        print(f"Metrics logged to MLflow run {run.info.run_id} "
              f"(experiment '{args.experiment_name}')", flush=True)

    # ---------- Consumer-метрики в S3 ----------
    metrics = {
        "run_id": run_id,
        "consumed": stats["consumed"],
        "batches": stats["batches"],
        "avg_batch_processing_ms": round(avg_batch_ms, 2),
        "max_batch_processing_ms": round(stats["max_latency_ms"], 2),
        "input_rows_per_second": round(input_rate, 2),
        "processed_rows_per_second": round(processed_rate, 2),
        "duration_s": elapsed,
        # качество модели на потоковых предсказаниях
        "tp": cm["tp"], "fp": cm["fp"], "tn": cm["tn"], "fn": cm["fn"],
        "n_labels": quality["n_labels"],
        "accuracy": quality["accuracy"],
        "precision": quality["precision"],
        "recall": quality["recall"],
        "f1": quality["f1"],
    }
    mdf = spark.createDataFrame([(k, str(v)) for k, v in metrics.items()],
                                ["metric", "value"])
    path = f"{args.metrics_output.strip('/')}/consumer_{run_id}.csv"
    mdf.coalesce(1).write.mode("overwrite").option("header", "true").csv(path)
    print(f"Consumer metrics written to {path}", flush=True)

    spark.stop()
    print("Streaming inference finished.")


if __name__ == "__main__":
    main()