#!/usr/bin/env python3
"""Spark job: replay historical fraud transactions into Kafka at a given pace.

Роль в hw_08: имитирует «реальное время». Читает исторические транзакции из S3
(ретроспективные данные), инженерит признаки (те же, что при обучении модели —
средние и счётчики по customer/terminal), а затем отправляет их как JSON-сообщения
в Kafka topic `fraud-transactions` с контролируемой скоростью (TPS).

Почему признаки считаются ЗДЕСЬ, а не в Spark Streaming:
  * оконные агрегаты по всей истории требовали бы stateful-стриминга (watermark,
    session windows) — сложно и хрупко в рамках ДЗ;
  * модель из MLflow принимает готовый вектор признаков, поэтому генератор
    отправляет уже обогащённые транзакции, а streaming job делает только
    transform (VectorAssembler + StandardScaler + RandomForest уже в MLPipeline).

Управление скоростью: сообщения шлются с драйвера через confluent-kafka Producer
с паузой 1/TPS между отправками — это даёт точный, воспроизводимый профиль
нагрузки для замера «при каком TPS consumer отстаёт».

Входной датасет — cleaned parquet из hw_07 (s3a://<bucket>/fraud_clean_parquet).
Если его нет — см. --input (можно указать исходные *.txt, схема парсится так же,
как в fraud_cleaning.py).

По завершении пишет CSV-метрики производителя в S3:
   s3a://<bucket>/kafka_test_metrics/producer_<run_id>.csv
"""

import argparse
import json
import sys
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window


def normalize_dataproc_args(argv):
    """Yandex Data Proc passes all PySpark job args as a single comma-joined
    token (e.g. '--bootstrap-servers,rc1-...,--topic,fraud-transactions').
    Split it back into separate argv entries before argparse sees it."""
    if len(argv) == 1 and argv[0].startswith("--") and "," in argv[0]:
        return argv[0].split(",")
    return argv


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = normalize_dataproc_args(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",
                        default="s3a://spark-bucket-ek/fraud_clean_parquet",
                        help="Input: cleaned Parquet (default) or raw txt glob")
    parser.add_argument("--bootstrap-servers", required=True,
                        help="Kafka bootstrap-servers, e.g. rc1a-xxx.internal:9091")
    parser.add_argument("--topic", default="fraud-transactions")
    parser.add_argument("--user", default="fraud-user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--tps", type=int, default=50,
                        help="Target transactions per second")
    parser.add_argument("--acks", type=str, default="all",
                        choices=["all", "0", "1"],
                        help="Producer acks: 'all' (durable) or '0' (max speed)")
    parser.add_argument("--max-messages", type=int, default=50000,
                        help="How many messages to send (0 = all available)")
    parser.add_argument("--sample-fraction", type=float, default=0.05,
                        help="Fraction of rows to keep before aggregation (speed)")
    parser.add_argument("--run-id", default=None,
                        help="Shared run id; generated if empty")
    parser.add_argument("--metrics-output",
                        default="s3a://spark-bucket-ek/kafka_test_metrics",
                        help="S3 dir to append producer metrics (CSV)")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


FEATURE_COLS = [
    "tx_amount", "tx_time_seconds", "tx_time_days",
    "avg_customer_amount", "customer_tx_count",
    "avg_terminal_amount", "terminal_tx_count", "customer_amount_ratio",
]

_TXT_SCHEMA = """
    transaction_id LONG,
    tx_datetime STRING,
    customer_id LONG,
    terminal_id LONG,
    tx_amount DOUBLE,
    tx_time_seconds LONG,
    tx_time_days INT,
    tx_fraud INT,
    tx_fraud_scenario INT
"""


def main():
    args = parse_args()

    spark = (SparkSession.builder
             .appName("KafkaFraudProducer")
             .config("spark.sql.adaptive.enabled", "true")
             .getOrCreate())
    spark._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
    spark._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
    spark._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")

    if args.input.endswith(".txt") or "*" in args.input:
        df = (spark.read
              .option("comment", "#")
              .option("header", "false")
              .schema(_TXT_SCHEMA)
              .csv(args.input))
    else:
        df = spark.read.parquet(args.input)
    print(f"Loaded {args.input}")

    if 0.0 < args.sample_fraction < 1.0:
        df = df.sample(withReplacement=False,
                       fraction=args.sample_fraction, seed=args.seed)
        print(f"Subsampled with fraction={args.sample_fraction}")

    # ---------- Feature engineering (аналогично fraud_train.py) ----------
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

    out_cols = ["transaction_id", "tx_datetime", "customer_id", "terminal_id",
                "tx_amount", "tx_time_seconds", "tx_time_days",
                "tx_fraud", "avg_customer_amount", "customer_tx_count",
                "avg_terminal_amount", "terminal_tx_count",
                "customer_amount_ratio"]

    if args.max_messages and args.max_messages > 0:
        feat = feat.orderBy(F.rand(seed=args.seed)).limit(args.max_messages)

    rows = feat.select(*out_cols).collect()
    total = len(rows)
    print(f"Ready to send {total} messages to Kafka topic '{args.topic}'")

    if total == 0:
        raise SystemExit("No messages to send - check --input / --sample-fraction")

    # ---------- Отправка в Kafka с контролем TPS ----------
    from confluent_kafka import Producer

    conf = {
        "bootstrap.servers": args.bootstrap_servers,
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": "SCRAM-SHA-512",
        "sasl.username": args.user,
        "sasl.password": args.password,
        "client.id": "fraud-producer",
        "linger.ms": 10,
        "acks": args.acks,
    }

    producer = Producer(conf)

    sent = 0
    t0 = time.time()
    interval = 1.0 / max(1, args.tps)

    def _json(i):
        r = rows[i]
        return {
            "transaction_id": r["transaction_id"],
            "tx_datetime": str(r["tx_datetime"]),
            "customer_id": r["customer_id"],
            "terminal_id": r["terminal_id"],
            "tx_amount": r["tx_amount"],
            "tx_time_seconds": r["tx_time_seconds"],
            "tx_time_days": r["tx_time_days"],
            "avg_customer_amount": float(r["avg_customer_amount"]),
            "customer_tx_count": int(r["customer_tx_count"]),
            "avg_terminal_amount": float(r["avg_terminal_amount"]),
            "terminal_tx_count": int(r["terminal_tx_count"]),
            "customer_amount_ratio": float(r["customer_amount_ratio"]),
            # Эталонная метка: нужна на consumer-стороне для оценки качества
            # (accuracy/f1/precision/recall) прямо в потоке. Дублируется с фичами
            # осознанно — модель на вход её не берет (см. FEATURE_COLS).
            "tx_fraud": int(r["tx_fraud"]),
            "ts_ms": int(time.time() * 1000),
        }

    # key - customer_id: обеспечивает партиционирование по клиенту,
    # диапазон читается консьюмером стабильно. value - JSON.
    try:
        for i in range(total):
            start_i = time.time()
            msg = _json(i)
            # key некритичен, партиционирование задаёт сам продюсер;
            # кладём transaction_id, чтобы корректно проверять дубликаты.
            producer.produce(args.topic, key=str(msg["transaction_id"]),
                             value=json.dumps(msg).encode("utf-8"))
            sent += 1
            # пауза для достижения целевого TPS (вычесть время сериализации)
            elapsed_i = (time.time() - start_i) * 1000
            delay = interval * 1000 - elapsed_i
            if delay > 0:
                time.sleep(delay / 1000.0)
            if sent % (args.tps * 10) == 0:
                producer.flush()
                print(f"  sent {sent}/{total} ({(sent/(time.time()-t0+1e-9)):.1f} msg/s)",
                      flush=True)
    finally:
        producer.flush()
    elapsed = time.time() - t0
    achieved = total / max(elapsed, 1e-9)
    print(f"Producer done: {total} messages in {elapsed:.1f}s -> "
          f"achieved {achieved:.1f} msg/s (target {args.tps} TPS)")

    # ---------- Метрики производителя в S3 ----------
    run_id = args.run_id or f"run-{int(time.time()*1000)}"
    metrics = {
        "run_id": run_id,
        "produced": total,
        "tps_target": args.tps,
        "tps_achieved": round(achieved, 2),
        "duration_s": round(elapsed, 2),
        "source": args.input,
    }
    mdf = spark.createDataFrame([(k, str(v)) for k, v in metrics.items()],
                                ["metric", "value"])
    path = f"{args.metrics_output.strip('/')}/producer_{run_id}.csv"
    mdf.coalesce(1).write.mode("overwrite").option("header", "true").csv(path)
    print(f"Producer metrics written to {path}")

    spark.stop()


if __name__ == "__main__":
    main()