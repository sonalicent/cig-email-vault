"""Runtime configuration for the email pipeline.

All values can be overridden by environment variables so the same container image runs
across environments without code changes (configuration over code).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Default S3 bucket that holds both the raw `.eml` inputs and the generated outputs.
_DEFAULT_BUCKET = "be-cig-vault-ds-raw"

# Input `.eml` objects live under `emails/<date>/`; outputs are partitioned by the email's
# own send date (D29) under `emails-extracted/`.
_DEFAULT_INPUT_PREFIX = "emails/"
_DEFAULT_OUTPUT_PREFIX = "emails-extracted/"

_DEFAULT_DEDUP_TABLE = "cig-vault-email-dedup"
_DEFAULT_METRICS_NAMESPACE = "CigVault/EmailPipeline"

# Attachments larger than this are skipped (defence against zip-bomb / oversized files).
_DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MiB


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Immutable pipeline configuration resolved from the environment."""

    source_bucket: str
    input_prefix: str
    output_prefix: str
    dedup_table: str
    metrics_namespace: str
    max_attachment_bytes: int

    @property
    def content_prefix(self) -> str:
        """Prefix for per-email Markdown/JSON output (`emails-extracted/content/`)."""
        return f"{self.output_prefix}content/"

    @property
    def attachments_prefix(self) -> str:
        """Prefix for per-attachment Markdown/JSON output (`emails-extracted/attachments/`)."""
        return f"{self.output_prefix}attachments/"


def load_config() -> Config:
    """Build a :class:`Config` from environment variables, falling back to defaults."""
    output_prefix = _env("OUTPUT_PREFIX", _DEFAULT_OUTPUT_PREFIX)
    if not output_prefix.endswith("/"):
        output_prefix += "/"

    input_prefix = _env("INPUT_PREFIX", _DEFAULT_INPUT_PREFIX)
    if not input_prefix.endswith("/"):
        input_prefix += "/"

    return Config(
        source_bucket=_env("SOURCE_BUCKET", _DEFAULT_BUCKET),
        input_prefix=input_prefix,
        output_prefix=output_prefix,
        dedup_table=_env("DEDUP_TABLE", _DEFAULT_DEDUP_TABLE),
        metrics_namespace=_env("METRICS_NAMESPACE", _DEFAULT_METRICS_NAMESPACE),
        max_attachment_bytes=_env_int("MAX_ATTACHMENT_BYTES", _DEFAULT_MAX_ATTACHMENT_BYTES),
    )
