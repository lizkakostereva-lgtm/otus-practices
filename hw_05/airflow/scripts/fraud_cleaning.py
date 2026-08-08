#!/usr/bin/env python3
"""Spark job: clean fraud transaction dataset.
Reads raw CSV from S3, performs cleaning, writes Parquet to S3.
"""

import argparse
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, when, to_timestamp, sum as _sum
from pyspark.storagelevel import StorageLevel


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="s3a://otus-mlops-source-data/*.txt",
                        help="Input S3 path glob")
    parser.add_argument("--output", default="s3a://spark-bucket-ek/fraud_clean_parquet",
                        help="Output S3 Parquet path")
    return parser.parse_args()


def main():
    args = parse_args()

    spark = (SparkSession.builder
             .appName("FraudCleaning")
             .getOrCreate())
    spark.conf.set("spark.sql.adaptive.enabled", "true")

    spark._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
    spark._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
    spark._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")

    schema = """
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

    df = (spark.read
          .option("comment", "#")
          .option("header", "false")
          .schema(schema)
          .csv(args.input)
          .repartition(64))
    df.persist(StorageLevel.MEMORY_AND_DISK)

    row_count = df.count()
    print(f"Loaded {row_count} rows from {args.input}")

    df_dates = df.withColumn("parsed_datetime", to_timestamp("tx_datetime"))

    bad_dates = df_dates.filter(col("parsed_datetime").isNull()).count()
    negative_amounts = df.filter(col("tx_amount") <= 0).count()
    duplicates = df.groupBy("transaction_id").count().filter(col("count") > 1).count()
    invalid_fraud = df.filter(~col("tx_fraud").isin(0, 1)).count()

    print(f"Bad dates: {bad_dates}")
    print(f"Negative amounts: {negative_amounts}")
    print(f"Duplicate IDs: {duplicates}")
    print(f"Invalid fraud flags: {invalid_fraud}")

    stats = df.selectExpr(
        "percentile_approx(tx_amount, 0.25) q1",
        "percentile_approx(tx_amount, 0.75) q3"
    ).collect()[0]
    q1, q3 = stats["q1"], stats["q3"]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    print(f"IQR bounds: lower={lower}, upper={upper}")

    median_amount = df.approxQuantile("tx_amount", [0.5], 0.01)[0]

    clean_df = (
        df_dates
        .filter(col("parsed_datetime").isNotNull())
        .filter(col("tx_fraud").isin(0, 1))
        .filter(col("customer_id") >= 0)
        .filter(col("terminal_id") >= 0)
        .dropDuplicates(["transaction_id"])
        .withColumn(
            "tx_amount",
            when(col("tx_amount") <= 0, median_amount)
            .when(col("tx_amount") < lower, lower)
            .when(col("tx_amount") > upper, upper)
            .otherwise(col("tx_amount"))
        )
        .drop("parsed_datetime")
    )

    clean_count = clean_df.count()
    print(f"Writing {clean_count} cleaned rows to {args.output}")

    clean_df.write \
        .mode("overwrite") \
        .option("compression", "snappy") \
        .parquet(args.output)

    print("Done.")
    spark.stop()


if __name__ == "__main__":
    main()
