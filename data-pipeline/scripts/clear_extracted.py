"""Delete extracted output objects from S3.

The pipeline writes generated Markdown/JSON under the output prefix
(`emails-extracted/content/` and `emails-extracted/attachments/`). This script removes
those objects so extraction can be re-run cleanly (typically paired with clearing the
dedup table and re-running the backfill). It never touches the raw `.eml` inputs under
`emails/`.

Usage (PowerShell):

    python scripts/clear_extracted.py --all --dry-run       # preview a full clear
    python scripts/clear_extracted.py --all                 # delete all extracted output
    python scripts/clear_extracted.py --prefix emails-extracted/content/2026-07-27/
    python scripts/clear_extracted.py --prefix emails-extracted/content/2026-07-27/ --dry-run

Configuration falls back to the same defaults as the runtime:

    --bucket  / SOURCE_BUCKET   (default: be-cig-vault-ds-raw)
    --prefix  / OUTPUT_PREFIX   (default: emails-extracted/)

As a safety guard, --prefix must sit within the output prefix so raw inputs can never be
deleted; pass --force to override for an unusual prefix.

Credentials/region come from the standard AWS SDK chain (env vars, profile, SSO).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import boto3

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("clear_extracted")

_DEFAULT_BUCKET = "be-cig-vault-ds-raw"
_DEFAULT_OUTPUT_PREFIX = "emails-extracted/"
_S3_DELETE_BATCH_MAX = 1000
# Infra is deployed in us-west-2; default here so the script targets the right region
# even when the terminal/profile defaults elsewhere (e.g. us-west-1).
_DEFAULT_REGION = "us-west-2"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SOURCE_BUCKET", _DEFAULT_BUCKET),
        help="Bucket holding the extracted output objects.",
    )

    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--all",
        action="store_true",
        help="Delete every object under the output prefix (default: emails-extracted/).",
    )
    scope.add_argument(
        "--prefix",
        help="Delete only objects under this key prefix.",
    )

    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or _DEFAULT_REGION,
        help=f"AWS region (default: {_DEFAULT_REGION}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow a --prefix outside the output prefix (bypasses the input-safety guard).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the objects that would be deleted without deleting anything.",
    )
    return parser.parse_args(argv)


def resolve_prefix(args: argparse.Namespace) -> str:
    """Return the effective delete prefix: --prefix if given, else the whole output prefix."""
    if args.prefix:
        return args.prefix
    return _output_prefix()


def _output_prefix() -> str:
    prefix = os.environ.get("OUTPUT_PREFIX", _DEFAULT_OUTPUT_PREFIX)
    return prefix if prefix.endswith("/") else prefix + "/"


def validate_prefix(prefix: str, force: bool) -> None:
    """Refuse to delete outside the output prefix unless --force is given."""
    if force:
        return
    guard = _output_prefix()
    if not prefix.startswith(guard):
        raise SystemExit(
            f"Refusing to delete under {prefix!r}: it is outside the output prefix "
            f"{guard!r}. Use --force to override."
        )


def iter_object_keys(s3_client, bucket: str, prefix: str):
    """Yield every object key under ``prefix``."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def delete_keys(s3_client, bucket: str, keys: list[str]) -> int:
    """Delete keys in batches of 1000. Returns the count successfully deleted."""
    deleted = 0
    for start in range(0, len(keys), _S3_DELETE_BATCH_MAX):
        chunk = keys[start : start + _S3_DELETE_BATCH_MAX]
        response = s3_client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
        )
        deleted += len(chunk) - len(response.get("Errors", []))
        for error in response.get("Errors", []):
            logger.error("Failed to delete %s: %s", error.get("Key"), error.get("Message"))
    return deleted


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    prefix = resolve_prefix(args)
    validate_prefix(prefix, args.force)

    session = boto3.session.Session(region_name=args.region)
    s3_client = session.client("s3")

    keys = list(iter_object_keys(s3_client, args.bucket, prefix))
    logger.info("Found %d object(s) under s3://%s/%s", len(keys), args.bucket, prefix)

    if not keys:
        return 0

    if args.dry_run:
        for key in keys:
            logger.info("[dry-run] would delete: %s", key)
        logger.info("[dry-run] %d object(s) would be deleted; nothing removed.", len(keys))
        return 0

    deleted = delete_keys(s3_client, args.bucket, keys)
    logger.info("Deleted %d/%d object(s).", deleted, len(keys))
    return 0 if deleted == len(keys) else 1


if __name__ == "__main__":
    sys.exit(main())
