#!/usr/bin/env python3
"""Kafka attack producer — ramps the event rate until the model API saturates.

The producer publishes URL events to the `urls` topic. The offered load follows
a linear ramp:

    tps(t) = start_tps + (max_tps - start_tps) * min(t / ramp_seconds, 1)

so within ``--ramp-seconds`` the in-cluster consumer is flooded, the model pods
burn CPU, the HPA scales 4 -> 6 and `UrlFraudScalingSaturated` eventually fires.

    python -m app.producer --start-tps 20 --max-tps 400 --ramp-seconds 300 --duration 900
"""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import sys
import time

from kafka.errors import NoBrokersAvailable

from app.common import all_urls, bootstrap, encode, topic
from kafka import KafkaProducer

logger = logging.getLogger("kafka.producer")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ramping Kafka URL producer (attack)")
    p.add_argument("--bootstrap", default=bootstrap())
    p.add_argument("--topic", default=topic())
    p.add_argument(
        "--start-tps", type=float, default=float(os.getenv("START_TPS", "20"))
    )
    p.add_argument("--max-tps", type=float, default=float(os.getenv("MAX_TPS", "400")))
    p.add_argument(
        "--ramp-seconds", type=float, default=float(os.getenv("RAMP_SECONDS", "300"))
    )
    p.add_argument(
        "--duration",
        type=float,
        default=float(os.getenv("DURATION", "900")),
        help="total run time in seconds (0 = run until interrupted)",
    )
    p.add_argument("--linger-ms", type=int, default=5)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def connect(bootstrap_servers: str, linger_ms: int) -> KafkaProducer:
    """Retry until the broker is reachable (it may still be starting)."""
    deadline = time.time() + 180
    while True:
        try:
            return KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: v,
                linger_ms=linger_ms,
                acks=1,
                retries=3,
            )
        except NoBrokersAvailable:
            if time.time() > deadline:
                raise
            logger.info("waiting for Kafka at %s ...", bootstrap_servers)
            time.sleep(5)


def target_tps(elapsed: float, start: float, peak: float, ramp: float) -> float:
    if ramp <= 0:
        return peak
    frac = min(elapsed / ramp, 1.0)
    return start + (peak - start) * frac


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )

    producer = connect(args.bootstrap, args.linger_ms)
    urls = itertools.cycle(all_urls())

    started = time.time()
    sent = 0
    next_send = started
    logger.info(
        "producing to %s@%s: %.0f -> %.0f tps over %.0fs (duration=%.0fs)",
        args.topic,
        args.bootstrap,
        args.start_tps,
        args.max_tps,
        args.ramp_seconds,
        args.duration,
    )

    try:
        while args.duration <= 0 or (time.time() - started) < args.duration:
            now = time.time()
            elapsed = now - started
            tps = target_tps(elapsed, args.start_tps, args.max_tps, args.ramp_seconds)
            interval = 1.0 / tps if tps > 0 else 1.0

            if now >= next_send:
                producer.send(args.topic, encode(next(urls)))
                sent += 1
                next_send += interval
                # Don't try to "catch up" if we fell behind the ramp target.
                if next_send < now:
                    next_send = now + interval
                if sent % 1000 == 0:
                    logger.info("sent=%d tps=%.0f elapsed=%.0fs", sent, tps, elapsed)
            else:
                time.sleep(min(interval, 0.05))
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        producer.flush(timeout=10)
        producer.close(timeout=10)

    logger.info(
        "done: sent=%d in %.0fs (avg %.1f tps)",
        sent,
        time.time() - started,
        sent / max(time.time() - started, 1),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
