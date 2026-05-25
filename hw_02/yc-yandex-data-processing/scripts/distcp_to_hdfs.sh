#!/bin/bash

BUCKET_NAME="spark-bucket-ek"

hadoop distcp \
  s3a://${BUCKET_NAME} \
  hdfs:///data

hdfs dfs -ls /data