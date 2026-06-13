#!/bin/bash

set -euo pipefail

SOURCE_BUCKET="otus-mlops-source-data"
HDFS_DIR="/user/data/fraud"

echo "Create HDFS directory..."
hdfs dfs -mkdir -p "${HDFS_DIR}"

echo "Start DistCp..."

hadoop distcp \
  -D fs.s3a.endpoint=storage.yandexcloud.net \
  -D fs.s3a.path.style.access=true \
  -D fs.s3a.connection.ssl.enabled=true \
  -m 20 \
  "s3a://${SOURCE_BUCKET}/" \
  "hdfs://${HDFS_DIR}"

echo "Check result..."

hdfs dfs -ls "${HDFS_DIR}"

echo "Done."