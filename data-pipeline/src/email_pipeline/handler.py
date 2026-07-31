"""Lambda entrypoint: SQS → S3 event → parse → split → dedup → write outputs (D21).

Processing is idempotent: DynamoDB conditional writes gate email output and wrapper-owned
attachment output. Failures are reported per SQS message via a partial-batch response so
only the failing message is retried / sent to the DLQ.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse

from .config import Config, load_config
from .dedup import DedupRegistry, compute_identity
from .eml_parser import parse_eml
from .extractors import extract
from .formatter import (
    attachment_key,
    attachment_metadata,
    content_key,
    email_metadata,
    render_attachment_markdown,
    render_email_markdown,
)
from .metrics import (
    ATTACHMENTS_EXTRACTED,
    DEDUP_SKIPS,
    EMAILS_PROCESSED,
    EMAILS_WRITTEN,
    EXTRACTION_FAILURES,
    Metrics,
)
from .models import ParsedEmail
from .s3_io import S3IO
from .thread_splitter import split_thread

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def handler(event: dict, context: object = None) -> dict:
    """Lambda entrypoint. Returns an SQS partial-batch-failure response."""
    config = load_config()
    s3 = S3IO(config.source_bucket)
    dedup = DedupRegistry(config.dedup_table)
    metrics = Metrics(config.metrics_namespace)

    failures: list[dict] = []
    try:
        for sqs_record in event.get("Records", []):
            message_id = sqs_record.get("messageId")
            try:
                for _bucket, key in _iter_object_keys(sqs_record):
                    process_object(key, s3=s3, dedup=dedup, metrics=metrics, config=config)
            except Exception:  # noqa: BLE001 - isolate per-message failures for retry/DLQ
                logger.exception("Failed processing SQS message %s", message_id)
                if message_id:
                    failures.append({"itemIdentifier": message_id})
    finally:
        metrics.flush()

    return {"batchItemFailures": failures}


def process_object(
    key: str,
    *,
    s3: S3IO,
    dedup: DedupRegistry,
    metrics: Metrics,
    config: Config,
) -> None:
    """Download, parse, split, dedup, and write outputs for a single `.eml` object."""
    raw = s3.get_object(key)
    parsed = parse_eml(raw)
    metrics.increment(EMAILS_PROCESSED)

    date = _date_from_key(key)
    eml_stem = _stem(key)

    units = split_thread(parsed.body)
    if not units:
        logger.info("No email units produced for %s", key)
        return

    for unit in units:
        if unit.is_wrapper:
            _apply_envelope(unit, parsed)

        identity = compute_identity(
            unit.message_id, unit.from_, unit.date, unit.subject, unit.body
        )
        hash8 = _short_hash(identity)
        out_key = content_key(config.content_prefix, date, unit.subject, hash8)

        claimed = dedup.claim(
            identity,
            {
                "source_key": key,
                "output_key": out_key,
                "subject": unit.subject,
                "date": unit.date,
            },
        )
        if not claimed:
            logger.info("Duplicate email skipped: %s", identity)
            metrics.increment(DEDUP_SKIPS)
            continue

        refs = []
        if unit.is_wrapper:
            refs = _process_attachments(
                parsed, key, date, eml_stem, s3=s3, metrics=metrics, config=config
            )

        s3.put_markdown(out_key, render_email_markdown(unit))
        s3.put_json(
            _json_key(out_key),
            email_metadata(unit, identity, key, out_key, refs),
        )
        metrics.increment(EMAILS_WRITTEN)


def _process_attachments(
    parsed: ParsedEmail,
    source_key: str,
    date: str,
    eml_stem: str,
    *,
    s3: S3IO,
    metrics: Metrics,
    config: Config,
) -> list[dict]:
    refs: list[dict] = []
    for attachment in parsed.attachments:
        if len(attachment.data) > config.max_attachment_bytes:
            logger.warning(
                "Attachment %s exceeds max size (%d bytes); skipping",
                attachment.filename,
                len(attachment.data),
            )
            metrics.increment(EXTRACTION_FAILURES)
            continue

        result = extract(attachment.filename, attachment.data)
        if result is None or not result.markdown.strip():
            continue

        out_key = attachment_key(config.attachments_prefix, date, eml_stem, attachment.filename)
        s3.put_markdown(out_key, render_attachment_markdown(attachment, source_key, result.markdown))
        s3.put_json(
            _json_key(out_key),
            attachment_metadata(attachment, source_key, out_key, result.json_data),
        )
        metrics.increment(ATTACHMENTS_EXTRACTED)
        refs.append(
            {
                "filename": attachment.filename,
                "output_key": out_key,
                "content_type": attachment.content_type,
            }
        )
    return refs


def _apply_envelope(unit, parsed: ParsedEmail) -> None:
    """Fill the wrapper unit's headers/message-id from the real MIME envelope."""
    unit.message_id = parsed.message_id
    unit.headers.setdefault("from", parsed.from_ or "")
    unit.headers.setdefault("to", parsed.to or "")
    unit.headers.setdefault("cc", parsed.cc or "")
    unit.headers.setdefault("subject", parsed.subject or "")
    unit.headers.setdefault("date", parsed.date or "")


def _iter_object_keys(sqs_record: dict):
    """Yield ``(bucket, key)`` for each S3 object referenced by an SQS record."""
    body = sqs_record.get("body")
    if body is None:
        s3_records = [sqs_record]
    else:
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            logger.warning("SQS record body is not valid JSON; skipping")
            return
        if payload.get("Event") == "s3:TestEvent":
            return
        s3_records = payload.get("Records", [])

    for record in s3_records:
        s3_info = record.get("s3")
        if not s3_info:
            continue
        bucket = s3_info["bucket"]["name"]
        key = urllib.parse.unquote_plus(s3_info["object"]["key"])
        yield bucket, key


def _date_from_key(key: str) -> str:
    match = _DATE_RE.search(key)
    return match.group(1) if match else "undated"


def _stem(key: str) -> str:
    base = key.rsplit("/", 1)[-1]
    if base.lower().endswith(".eml"):
        return base[:-4]
    return base


def _short_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def _json_key(markdown_key: str) -> str:
    return markdown_key[:-3] + ".json" if markdown_key.endswith(".md") else markdown_key + ".json"
