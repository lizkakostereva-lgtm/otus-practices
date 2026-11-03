import os

import pandas as pd
import yaml

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None


def load_config(path="params.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def _s3_client():
    import boto3

    return boto3.client("s3", endpoint_url=S3_ENDPOINT_URL)


def download_from_s3(local_path: str, key: str) -> bool:
    """Download an object from the configured data bucket.

    Disabled when S3_DATA_BUCKET is not set, so local development keeps working.
    """
    bucket = os.getenv("S3_DATA_BUCKET")
    if not bucket:
        return False
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    _s3_client().download_file(bucket, key, local_path)
    return True


def upload_to_s3(local_path: str, key: str) -> bool:
    bucket = os.getenv("S3_DATA_BUCKET")
    if not bucket or not os.path.exists(local_path):
        return False
    _s3_client().upload_file(local_path, bucket, key)
    return True


def load_raw_data(config=None):
    if config is None:
        config = load_config()
    path = config["data"]["raw_path"]
    if not os.path.exists(path):
        key = os.getenv("S3_RAW_KEY", "raw/WA_Fn-UseC_-Telco-Customer-Churn.csv")
        download_from_s3(path, key)
    return pd.read_csv(path)
