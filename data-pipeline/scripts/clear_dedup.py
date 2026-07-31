"""Delete items from the DynamoDB dedup table.

The pipeline records one item per processed email identity in the dedup table
(partition key ``pk``). Clearing entries lets those emails be re-processed on the
next run (e.g. after a backfill). This script deletes items in three modes:

    - full clear:    every item in the table
    - by key:        one or more exact ``pk`` values (--key, repeatable)
    - by prefix:     items whose ``source_key`` begins with a given prefix

Usage (PowerShell):

    python scripts/clear_dedup.py --all --dry-run           # preview a full clear
    python scripts/clear_dedup.py --all                     # delete everything
    python scripts/clear_dedup.py --key "sha256:abc..." --key "<message-id>"
    python scripts/clear_dedup.py --prefix emails/2026-07-27/   # one date partition
    python scripts/clear_dedup.py --prefix emails/2026-07-27/ --dry-run

Configuration falls back to the same defaults as the runtime:

    --table  / DEDUP_TABLE  (default: cig-vault-email-dedup)

Credentials/region come from the standard AWS SDK chain (env vars, profile, SSO).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import boto3

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("clear_dedup")

_DEFAULT_DEDUP_TABLE = "cig-vault-email-dedup"
# Infra is deployed in us-west-2; default here so the script targets the right region
# even when the terminal/profile defaults elsewhere (e.g. us-west-1).
_DEFAULT_REGION = "us-west-2"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        default=os.environ.get("DEDUP_TABLE", _DEFAULT_DEDUP_TABLE),
        help="DynamoDB dedup table name.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or _DEFAULT_REGION,
        help=f"AWS region (default: {_DEFAULT_REGION}).",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--all",
        action="store_true",
        help="Delete every item in the table.",
    )
    mode.add_argument(
        "--key",
        action="append",
        dest="keys",
        metavar="PK",
        help="Exact pk value to delete. Repeat to delete several.",
    )
    mode.add_argument(
        "--prefix",
        help="Delete items whose source_key begins with this prefix.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the items that would be deleted without deleting anything.",
    )
    return parser.parse_args(argv)


def scan_all_keys(table) -> list[str]:
    """Return every pk in the table."""
    keys: list[str] = []
    kwargs = {"ProjectionExpression": "pk"}
    while True:
        response = table.scan(**kwargs)
        keys.extend(item["pk"] for item in response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if not last:
            return keys
        kwargs["ExclusiveStartKey"] = last


def scan_keys_by_prefix(table, prefix: str) -> list[str]:
    """Return pks of items whose ``source_key`` begins with ``prefix``."""
    keys: list[str] = []
    kwargs = {
        "ProjectionExpression": "pk",
        "FilterExpression": "begins_with(source_key, :p)",
        "ExpressionAttributeValues": {":p": prefix},
    }
    while True:
        response = table.scan(**kwargs)
        keys.extend(item["pk"] for item in response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if not last:
            return keys
        kwargs["ExclusiveStartKey"] = last


def resolve_keys(table, args: argparse.Namespace) -> list[str]:
    if args.all:
        return scan_all_keys(table)
    if args.prefix:
        return scan_keys_by_prefix(table, args.prefix)
    return list(args.keys or [])


def delete_keys(table, keys: list[str]) -> int:
    """Batch-delete the given pks. Returns the count submitted for deletion."""
    with table.batch_writer() as batch:
        for pk in keys:
            batch.delete_item(Key={"pk": pk})
    return len(keys)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = boto3.session.Session(region_name=args.region)
    table = session.resource("dynamodb").Table(args.table)

    keys = resolve_keys(table, args)
    logger.info("Matched %d item(s) in table %s", len(keys), args.table)

    if not keys:
        return 0

    if args.dry_run:
        for pk in keys:
            logger.info("[dry-run] would delete: %s", pk)
        logger.info("[dry-run] %d item(s) would be deleted; nothing removed.", len(keys))
        return 0

    deleted = delete_keys(table, keys)
    logger.info("Deleted %d item(s).", deleted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
