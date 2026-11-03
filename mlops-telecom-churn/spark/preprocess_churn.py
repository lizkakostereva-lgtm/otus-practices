"""Spark job (full variant): clean the Telco churn raw CSV into Parquet on S3.

Runs on a temporary Yandex Data Processing cluster, which is created/destroyed
by the Airflow DAG (airflow/dags/churn_spark_dataproc_dag.py). The cleaning
logic mirrors src/data/preprocess.py (pandas) so both variants produce the
same dataset semantics:
  - TotalCharges -> numeric, blanks -> median
  - Churn        -> "Yes"/"No" mapped to 1/0
"""

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def main() -> None:
    parser = argparse.ArgumentParser(description="Telco churn: clean raw data (Spark)")
    parser.add_argument("--input", required=True, help="s3a:// path to the raw CSV")
    parser.add_argument("--output", required=True, help="s3a:// path for cleaned Parquet")
    parser.add_argument("--aws-access-key-id", default=None)
    parser.add_argument("--aws-secret-access-key", default=None)
    args = parser.parse_args()

    spark = SparkSession.builder.appName("churn-preprocess").getOrCreate()
    hadoop = spark._jsc.hadoopConfiguration()  # noqa: SLF001

    # Data Proc nodes read the dataset from Object Storage over s3a.
    hadoop.set("fs.s3a.endpoint", "storage.yandexcloud.net")
    if args.aws_access_key_id and args.aws_secret_access_key:
        hadoop.set("fs.s3a.access.key", args.aws_access_key_id)
        hadoop.set("fs.s3a.secret.key", args.aws_secret_access_key)
        hadoop.set("fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")

    df = spark.read.option("header", "true").csv(args.input)

    # TotalCharges: blanks / non-numeric -> median of parsed values.
    df = df.withColumn("TotalCharges", F.col("TotalCharges").cast("double"))
    median = df.approxQuantile("TotalCharges", [0.5], 0.0)[0]
    df = df.fillna({"TotalCharges": median})

    # Target: Churn Yes/No -> 1/0, keep the rest as strings; categorical
    # encoding happens in the training job (StringIndexer/OneHotEncoder).
    df = df.withColumn("Churn", F.when(F.col("Churn") == "Yes", 1).otherwise(0))

    df = df.dropDuplicates(["customerID"])

    df.write.mode("overwrite").parquet(args.output)
    print(f"cleaned rows={df.count()} -> {args.output}")

    spark.stop()


if __name__ == "__main__":
    main()