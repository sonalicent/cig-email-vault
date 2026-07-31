"""One-time backfill: enqueue pre-existing `.eml` objects onto the ingest queue.

The live pipeline runs S3 (`emails/<date>/*.eml`) -> SQS -> Lambda. New uploads are
processed automatically, but objects that already existed before the notification was
wired up are never seen. This script lists those objects and sends one synthetic S3
`ObjectCreated` message per object to the ingest SQS queue, so the already-deployed
Lambda processes them exactly as it would a live event. DynamoDB dedup makes re-runs
idempotent, so it is safe to run more than once.

Usage (PowerShell):

    python scripts/backfill.py --all                # enqueue everything under emails/
    python scripts/backfill.py --all --dry-run      # list what would be enqueued, send nothing
    python scripts/backfill.py --prefix emails/2026-07-27/   # limit to one date partition
    python scripts/backfill.py --queue-url https://sqs.us-west-2.amazonaws.com/123/cig-vault-eml-ingest --all

Configuration falls back to the same defaults as the runtime:

    --bucket     / SOURCE_BUCKET   (default: be-cig-vault-ds-raw)
    --prefix     / INPUT_PREFIX    (default: emails/)
    --queue-url  / INGEST_QUEUE_URL

The queue URL is resolved in this order: --queue-url, INGEST_QUEUE_URL env var,
then a lookup of --queue-name (default: cig-vault-eml-ingest) via GetQueueUrl.
Credentials/region come from the standard AWS SDK chain (env vars, profile, SSO).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import boto3

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill")

_DEFAULT_BUCKET = "be-cig-vault-ds-raw"
_DEFAULT_INPUT_PREFIX = "emails/"
_DEFAULT_QUEUE_NAME = "cig-vault-eml-ingest"
_SQS_BATCH_MAX = 10
# Infra is deployed in us-west-2; default here so the script targets the right region
# even when the terminal/profile defaults elsewhere (e.g. us-west-1).
_DEFAULT_REGION = "us-west-2"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SOURCE_BUCKET", _DEFAULT_BUCKET),
        help="Source bucket holding the .eml inputs.",
    )

    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--all",
        action="store_true",
        help="Enqueue every .eml under the input prefix (default: emails/).",
    )
    scope.add_argument(
        "--prefix",
        help="Enqueue only .eml objects under this key prefix.",
    )

    parser.add_argument(
        "--queue-url",
        default=os.environ.get("INGEST_QUEUE_URL"),
        help="Ingest queue URL. If omitted, resolved from --queue-name via GetQueueUrl.",
    )
    parser.add_argument(
        "--queue-name",
        default=os.environ.get("INGEST_QUEUE_NAME", _DEFAULT_QUEUE_NAME),
        help="Ingest queue name, used to resolve the URL when --queue-url is not given.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or _DEFAULT_REGION,
        help=f"AWS region (default: {_DEFAULT_REGION}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the objects that would be enqueued without sending any messages.",
    )
    return parser.parse_args(argv)


def resolve_prefix(args: argparse.Namespace) -> str:
    """Return the effective scan prefix: --prefix if given, else the whole input prefix."""
    if args.prefix:
        return args.prefix
    return os.environ.get("INPUT_PREFIX", _DEFAULT_INPUT_PREFIX)


def iter_eml_keys(s3_client, bucket: str, prefix: str):
    """Yield every key under ``prefix`` ending in ``.eml`` (case-insensitive)."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(".eml"):
                yield key


def build_message_body(bucket: str, key: str) -> str:
    """Return an S3 ObjectCreated notification body matching the live event shape.

    The handler's ``_iter_object_keys`` reads ``body`` as JSON and pulls
    ``Records[].s3.bucket.name`` and ``Records[].s3.object.key``.
    """
    return json.dumps(
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventName": "ObjectCreated:Backfill",
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": key},
                    },
                }
            ]
        }
    )


def resolve_queue_url(sqs_client, args: argparse.Namespace) -> str:
    if args.queue_url:
        return args.queue_url
    return sqs_client.get_queue_url(QueueName=args.queue_name)["QueueUrl"]


def send_batches(sqs_client, queue_url: str, keys: list[str], bucket: str) -> int:
    """Send keys to SQS in batches of 10. Returns the count successfully enqueued."""
    sent = 0
    for start in range(0, len(keys), _SQS_BATCH_MAX):
        chunk = keys[start : start + _SQS_BATCH_MAX]
        entries = [
            {"Id": str(i), "MessageBody": build_message_body(bucket, key)}
            for i, key in enumerate(chunk)
        ]
        response = sqs_client.send_message_batch(QueueUrl=queue_url, Entries=entries)
        sent += len(response.get("Successful", []))
        for failure in response.get("Failed", []):
            failed_key = chunk[int(failure["Id"])]
            logger.error("Failed to enqueue %s: %s", failed_key, failure.get("Message"))
    return sent


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    prefix = resolve_prefix(args)
    session = boto3.session.Session(region_name=args.region)
    s3_client = session.client("s3")

    keys = list(iter_eml_keys(s3_client, args.bucket, prefix))
    logger.info("Found %d .eml object(s) under s3://%s/%s", len(keys), args.bucket, prefix)

    if not keys:
        return 0

    if args.dry_run:
        for key in keys:
            logger.info("[dry-run] would enqueue: %s", key)
        logger.info("[dry-run] %d object(s) would be enqueued; nothing sent.", len(keys))
        return 0

    sqs_client = session.client("sqs")
    queue_url = resolve_queue_url(sqs_client, args)
    logger.info("Enqueuing to %s", queue_url)

    sent = send_batches(sqs_client, queue_url, keys, args.bucket)
    logger.info("Enqueued %d/%d object(s).", sent, len(keys))
    return 0 if sent == len(keys) else 1


if __name__ == "__main__":
    sys.exit(main())
