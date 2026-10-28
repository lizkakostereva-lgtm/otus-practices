#!/usr/bin/env python3
"""Kafka consumer that turns URL events into inference load on the model API.

Reads the `urls` topic and, with a thread pool, issues batched requests to the
service's ``/api/v1/predict/batch`` endpoint. This is what actually drives CPU
usage on the model pods (and therefore the HPA) during the attack.

    python -m app.consumer --api-url http://url-fraud-api/api/v1 --concurrency 32 --duration 900
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests
from kafka.errors import NoBrokersAvailable

from app.common import bootstrap, decode, topic
from kafka import KafkaConsumer

logger = logging.getLogger("kafka.consumer")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Inference consumer (attack load generator)"
    )
    p.add_argument("--bootstrap", default=bootstrap())
    p.add_argument("--topic", default=topic())
    p.add_argument(
        "--group-id", default=os.getenv("KAFKA_GROUP", "url-fraud-inference")
    )
    p.add_argument(
        "--api-url", default=os.getenv("API_URL", "http://url-fraud-api/api/v1")
    )
    p.add_argument("--batch-size", type=int, default=int(os.getenv("BATCH_SIZE", "16")))
    p.add_argument(
        "--concurrency", type=int, default=int(os.getenv("CONCURRENCY", "32"))
    )
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument(
        "--duration", type=float, default=float(os.getenv("DURATION", "900"))
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def make_consumer(
    bootstrap_servers: str, topic_name: str, group_id: str
) -> KafkaConsumer:
    deadline = time.time() + 180
    while True:
        try:
            return KafkaConsumer(
                topic_name,
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                value_deserializer=decode,
                consumer_timeout_ms=1000,
            )
        except NoBrokersAvailable:
            if time.time() > deadline:
                raise
            logger.info("waiting for Kafka at %s ...", bootstrap_servers)
            time.sleep(5)


def send_batch(
    session: requests.Session, api_url: str, urls: list[str], timeout: float
) -> int:
    try:
        resp = session.post(
            f"{api_url}/predict/batch", json={"urls": urls}, timeout=timeout
        )
        return resp.status_code
    except requests.RequestException as exc:  # noqa: PERF203 - count, don't crash
        logger.debug("request failed: %s", exc)
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )

    consumer = make_consumer(args.bootstrap, args.topic, args.group_id)
    session = requests.Session()
    started = time.time()

    consumed = 0
    ok = 0
    pending: deque = deque()
    window: deque[float] = deque(maxlen=2000)

    logger.info(
        "consuming %s@%s -> %s/predict/batch (batch=%d concurrency=%d duration=%.0fs)",
        args.topic,
        args.bootstrap,
        args.api_url,
        args.batch_size,
        args.concurrency,
        args.duration,
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while args.duration <= 0 or (time.time() - started) < args.duration:
            batch: list[str] = []
            for message in consumer:
                batch.append(message.value)
                if len(batch) >= args.batch_size:
                    break
                if time.time() - started > args.duration > 0:
                    break

            if batch:
                consumed += len(batch)
                window.append(time.time())
                pending.append(
                    pool.submit(send_batch, session, args.api_url, batch, args.timeout)
                )

            # Reap finished futures so the queue does not grow unbounded.
            while pending and pending[0].done():
                if pending.popleft().result() == 200:
                    ok += 1

            if consumed and consumed % 5000 < args.batch_size:
                rate = sum(1 for t in window if time.time() - t <= 5) / 5.0
                logger.info(
                    "consumed=%d ok_batches=%d approx_urls/s=%.0f", consumed, ok, rate
                )

    logger.info("done: consumed=%d urls, %d successful batches", consumed, ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
