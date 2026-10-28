"""Shared helpers for the Kafka attack simulator (producer + consumer)."""

from __future__ import annotations

import json
import os

# A small fixed corpus of URLs. Mixing obviously-benign and obviously-malicious
# samples, plus a few random paths, keeps the load realistic without shipping
# the whole training CSV in the image.
BENIGN_URLS = [
    "https://www.google.com/search?q=weather",
    "https://github.com/kubernetes/kubernetes",
    "https://docs.python.org/3/library/asyncio.html",
    "https://yandex.cloud/ru/docs/managed-kubernetes/",
    "https://en.wikipedia.org/wiki/Random_forest",
    "https://stackoverflow.com/questions/tagged/scikit-learn",
    "https://www.croc.ru/",
    "https://otus.ru/lessons/mlops/",
    "https://huggingface.co/models",
    "https://releases.ubuntu.com/24.04/",
    "https://www.python.org/downloads/",
    "https://prometheus.io/docs/introduction/overview/",
    "https://grafana.com/docs/grafana/latest/",
    "https://airflow.apache.org/docs/apache-airflow/stable/",
    "https://mlflow.org/docs/latest/index.html",
]

MALICIOUS_URLS = [
    "http://192.168.1.1/login-secure.php?user=admin",
    "http://paypal-verify-account.tk/signin",
    "http://free-gift-card.xyz/claim?id=998877",
    "http://secure-bank.update-access.ru/verify",
    "http://appleid-locked.support-login.info/",
    "http://1.2.3.4/../../../etc/passwd",
    "http://download-crack.exe.badhost.top/setup.exe",
    "http://login.microsoftonline.com.phish.example/auth",
    "http://bit.ly/2xQ9zzz",
    "http://mega-uploader.com/files/0xDEADBEEF/xxx.pdf.exe",
    "http://xn--80ak6aa92e.com/",
    "http://tinyurl.com/free-crypto-now",
    "http://adult-teen-pics.ru/",
    "http://win-lottery-2026.click/prize",
    "http://update-flash-player.download/install",
]


def all_urls() -> list[str]:
    """Return the mixed URL corpus (malicious entries duplicated to add signal)."""
    return BENIGN_URLS + MALICIOUS_URLS + MALICIOUS_URLS


def topic() -> str:
    return os.getenv("KAFKA_TOPIC", "urls")


def bootstrap() -> str:
    return os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")


def encode(url: str) -> bytes:
    return json.dumps({"url": url}).encode("utf-8")


def decode(payload: bytes) -> str:
    return json.loads(payload.decode("utf-8"))["url"]
