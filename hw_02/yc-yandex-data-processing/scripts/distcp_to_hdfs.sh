#!/bin/bash

set -euo pipefail

BUCKET_NAME="spark-bucket-ek"

hdfs dfs -mkdir -p /user/root/data

hadoop distcp \
  -D fs.s3a.endpoint=storage.yandexcloud.net \
  -D fs.s3a.path.style.access=true \
  -m 20 \
  s3a://${BUCKET_NAME}/ \
  hdfs:///user/root/data/

hdfs dfs -ls /user/root/data