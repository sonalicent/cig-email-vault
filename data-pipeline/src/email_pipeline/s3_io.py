"""Thin S3 helpers: read the source `.eml`, write Markdown and JSON outputs."""

from __future__ import annotations

import json

import boto3

_MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class S3IO:
    """Wrapper over the S3 client scoped to a single bucket."""

    def __init__(self, bucket: str, s3_client=None) -> None:
        self._bucket = bucket
        self._client = s3_client or boto3.client("s3")

    def get_object(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def put_markdown(self, key: str, markdown: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=markdown.encode("utf-8"),
            ContentType=_MARKDOWN_CONTENT_TYPE,
        )

    def put_json(self, key: str, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str, indent=2).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=_JSON_CONTENT_TYPE,
        )
