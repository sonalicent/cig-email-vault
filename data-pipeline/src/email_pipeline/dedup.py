"""Email identity + DynamoDB-backed deduplication (D6, D7, D8).

The same email recurs across forwards. Identity is the RFC 5322 ``Message-ID`` when
present, otherwise a deterministic SHA-256 over a normalized
``From + Date + Subject + body-prefix``. A DynamoDB conditional ``PutItem``
(``attribute_not_exists(pk)``) gives atomic first-writer-wins idempotency.
"""

from __future__ import annotations

import hashlib
import re
import time

import boto3
from botocore.exceptions import ClientError

_WHITESPACE_RE = re.compile(r"\s+")
_BODY_PREFIX_CHARS = 2000
_SCHEMA_VERSION = 1


def compute_identity(
    message_id: str | None,
    from_: str,
    date: str,
    subject: str,
    body: str,
) -> str:
    """Return a stable identity string for an email unit."""
    if message_id and message_id.strip():
        return message_id.strip()

    normalized = "\n".join(
        [
            (from_ or "").strip().lower(),
            (date or "").strip().lower(),
            (subject or "").strip().lower(),
            _normalize_body(body)[:_BODY_PREFIX_CHARS],
        ]
    )
    digest = hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()
    return f"sha256:{digest}"


def _normalize_body(body: str) -> str:
    return _WHITESPACE_RE.sub(" ", body or "").strip().lower()


class DedupRegistry:
    """Thin wrapper over the DynamoDB dedup table."""

    def __init__(self, table_name: str, dynamodb_resource=None) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def claim(self, identity: str, attributes: dict | None = None) -> bool:
        """Atomically claim ``identity``.

        Returns ``True`` if this caller is the first writer (new email → process it),
        ``False`` if the identity already exists (duplicate → skip).
        """
        item: dict[str, object] = {
            "pk": identity,
            "created_at": int(time.time()),
            "schema_version": _SCHEMA_VERSION,
        }
        if attributes:
            item.update({k: v for k, v in attributes.items() if v is not None})

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
