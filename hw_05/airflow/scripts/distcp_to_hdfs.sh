#!/bin/bash
set -euo pipefail

SOURCE_BUCKET="otus-mlops-source-data"
HDFS_DIR="/user/data/fraud"

echo "[$(date)] Creating HDFS directory..."
hdfs dfs -mkdir -p "${HDFS_DIR}"

echo "[$(date)] Starting DistCp from s3a://${SOURCE_BUCKET}/ to hdfs:///${HDFS_DIR}..."

hadoop distcp \
  -D fs.s3a.endpoint=storage.yandexcloud.net \
  -D fs.s3a.path.style.access=true \
  -D fs.s3a.connection.ssl.enabled=true \
  -m 20 \
  "s3a://${SOURCE_BUCKET}/" \
  "hdfs:///${HDFS_DIR}"

echo "[$(date)] Verifying..."
hdfs dfs -ls "${HDFS_DIR}"

echo "[$(date)] DistCp completed."
