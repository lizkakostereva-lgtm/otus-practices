from pyspark.sql import SparkSession
from pyspark.sql.functions import *

INPUT_PATH = "s3a://spark-bucket-ek/*.csv"
OUTPUT_PATH = "s3a://spark-bucket-ek/fraud_clean_parquet"
INPUT_DATA =  "rc1a-dataproc-m-lj7q01ef2tjqlm9m.mdb.yandexcloud.net:8020/user/data/fraud"

spark = SparkSession.builder.appName("FraudCleaning").getOrCreate()
spark.conf.set("spark.sql.adaptive.enabled","true")
spark.conf.set("spark.sql.shuffle.partitions","64")

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

df = spark.read.schema(schema).csv(INPUT_PATH)

df_dates = df.withColumn("parsed_datetime", to_timestamp("tx_datetime"))

stats = df.selectExpr(
"percentile_approx(tx_amount,0.25) q1",
"percentile_approx(tx_amount,0.75) q3"
).collect()[0]

q1,q3 = stats["q1"],stats["q3"]
iqr = q3-q1
lower = q1-1.5*iqr
upper = q3+1.5*iqr

median_amount = df.approxQuantile("tx_amount",[0.5],0.01)[0]

clean_df = (
 df_dates
 .filter(col("parsed_datetime").isNotNull())
 .filter(col("tx_fraud").isin(0,1))
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

clean_df.write.mode("overwrite").option("compression","snappy").parquet(OUTPUT_PATH)
spark.stop()
