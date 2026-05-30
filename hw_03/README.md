# Fraud Transactions Processing with Yandex Data Proc

## Infrastructure

Terraform creates:

* Yandex Object Storage bucket
* Service Account
* IAM roles
* Security Group
* Yandex Data Proc Spark cluster

Cluster configuration:

### Master

* s3-c2-m8
* 40 GB disk

### Data

* s3-c4-m16
* 3 hosts
* 128 GB disk

## Public Bucket

Bucket URL:

https://storage.yandexcloud.net/spark-bucket-ek

## Upload source data to HDFS

```bash
bash scripts/distcp_to_hdfs.sh
```

Alternative:

```bash
bash scripts/download_to_hdfs.sh
```

## Run quality analysis

```bash
spark-submit \
  --master yarn \
  spark/quality_report.py
```

## Run cleaning job

```bash
spark-submit \
  --master yarn \
  spark/clean_data.py
```

## Run JupyterLab

SSH to master node:

```bash
ssh yc-user@MASTER_PUBLIC_IP
```

Start JupyterLab:

```bash
jupyter lab \
  --ip=0.0.0.0 \
  --port=8888 \
  --no-browser \
  --allow-root
```

Open browser:

```text
http://MASTER_PUBLIC_IP:8888
```

## Output Data

Cleaned dataset is stored in parquet format:

```text
s3a://spark-bucket-ek/cleaned-data
```
