#!/usr/bin/env python3
"""Promote the newest registered model version to the `champion` alias.

Run as its own Airflow task so the "train" and "release" steps are separately
auditable (and the release can be gated / re-run independently). The service
resolves the `champion` alias at startup — see `app/main.py`.

    python -m mlops.promote \
        --tracking-uri http://mlflow.url-fraud.svc:5000 \
        --model-name url-fraud-detector --alias champion
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("mlops.promote")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote latest model version to an alias"
    )
    parser.add_argument("--tracking-uri", default=os.getenv("MLFLOW_TRACKING_URI"))
    parser.add_argument(
        "--model-name", default=os.getenv("MLFLOW_MODEL_NAME", "url-fraud-detector")
    )
    parser.add_argument("--alias", default=os.getenv("MLFLOW_ALIAS", "champion"))
    parser.add_argument(
        "--version",
        default=None,
        help="Explicit version to promote; default = newest registered version",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def _pick_version(client, model_name: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        raise RuntimeError(f"model {model_name!r} has no registered versions yet")
    return max(versions, key=lambda v: int(v.version)).version


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )

    from mlflow.tracking import MlflowClient

    import mlflow

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)
    client = MlflowClient()

    version = _pick_version(client, args.model_name, args.version)
    client.set_registered_model_alias(args.model_name, args.alias, version)
    try:
        client.set_model_version_tag(args.model_name, version, "promoted_by", "airflow")
    except Exception:  # noqa: BLE001 - tagging is best-effort telemetry
        logger.warning("could not tag model version %s", version, exc_info=True)

    logger.info("alias %s -> %s v%s", args.alias, args.model_name, version)
    print(
        json.dumps(
            {"model_name": args.model_name, "version": version, "alias": args.alias}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
