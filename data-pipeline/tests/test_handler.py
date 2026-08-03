import json

import boto3
import pytest
from moto import mock_aws

from conftest import read_fixture

from email_pipeline.config import load_config
from email_pipeline.handler import (
    _date_from_key,
    _iter_object_keys,
    _stem,
    handler,
    process_object,
)
from email_pipeline.metrics import Metrics

BUCKET = "test-bucket"
TABLE = "test-dedup"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.puts: dict[str, object] = {}

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_markdown(self, key: str, markdown: str) -> None:
        self.puts[key] = markdown

    def put_json(self, key: str, data: object) -> None:
        self.puts[key] = data


class FakeDedup:
    def __init__(self):
        self.seen: set[str] = set()

    def claim(self, identity: str, attributes: dict | None = None) -> bool:
        if identity in self.seen:
            return False
        self.seen.add(identity)
        return True


def _run(source_key: str, s3: FakeS3, dedup: FakeDedup) -> None:
    process_object(
        source_key,
        s3=s3,
        dedup=dedup,
        metrics=Metrics("test"),
        config=load_config(),
    )


# --- helper unit tests -------------------------------------------------------

def test_date_from_key():
    assert _date_from_key("emails/2026-07-27/abc.eml") == "2026-07-27"
    assert _date_from_key("emails/no-date/abc.eml") == "undated"


def test_stem_strips_eml_extension():
    assert _stem("emails/2026-07-27/ENTRY==.eml") == "ENTRY=="


def test_iter_object_keys_parses_sqs_wrapped_s3_event():
    s3_event = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "emails/x+y.eml"}}}]}
    sqs_record = {"messageId": "m1", "body": json.dumps(s3_event)}
    keys = list(_iter_object_keys(sqs_record))
    assert keys == [("b", "emails/x y.eml")]


def test_iter_object_keys_skips_s3_test_event():
    sqs_record = {"body": json.dumps({"Event": "s3:TestEvent"})}
    assert list(_iter_object_keys(sqs_record)) == []


# --- process_object with fakes ----------------------------------------------

def test_process_direct_email_writes_one_content_pair():
    key = "emails/2026-07-27/direct.eml"
    s3 = FakeS3({key: read_fixture("direct_plain.eml")})
    dedup = FakeDedup()
    _run(key, s3, dedup)

    md_keys = [k for k in s3.puts if k.endswith(".md")]
    json_keys = [k for k in s3.puts if k.endswith(".json")]
    assert len(md_keys) == 1
    assert len(json_keys) == 1
    assert md_keys[0].startswith("emails-extracted/content/2026-07-27/")
    assert "Water demand" in s3.puts[md_keys[0]] or "water demand" in s3.puts[md_keys[0]].lower()


def test_date_partition_uses_header_date_over_key():
    # Key encodes 2026-07-27 but the email's own Date header is 2026-07-15.
    key = "emails/2026-07-27/earlier.eml"
    s3 = FakeS3({key: read_fixture("header_date_differs.eml")})
    _run(key, s3, FakeDedup())

    md_keys = [k for k in s3.puts if k.endswith(".md")]
    assert len(md_keys) == 1
    assert md_keys[0].startswith("emails-extracted/content/2026-07-15/")


def test_date_partition_falls_back_to_key_when_header_date_missing():
    # No Date header -> fall back to the date encoded in the input key.
    key = "emails/2026-07-27/no-date.eml"
    s3 = FakeS3({key: read_fixture("no_date.eml")})
    _run(key, s3, FakeDedup())

    md_keys = [k for k in s3.puts if k.endswith(".md")]
    assert len(md_keys) == 1
    assert md_keys[0].startswith("emails-extracted/content/2026-07-27/")


def test_process_forward_splits_into_two_emails():
    key = "emails/2026-07-27/fwd.eml"
    s3 = FakeS3({key: read_fixture("forward_plain.eml")})
    dedup = FakeDedup()
    _run(key, s3, dedup)

    content_md = [k for k in s3.puts if k.startswith("emails-extracted/content/") and k.endswith(".md")]
    assert len(content_md) == 2


def test_reprocessing_same_email_is_deduped():
    key = "emails/2026-07-27/direct.eml"
    dedup = FakeDedup()

    s3a = FakeS3({key: read_fixture("direct_plain.eml")})
    _run(key, s3a, dedup)

    s3b = FakeS3({key: read_fixture("direct_plain.eml")})
    _run(key, s3b, dedup)  # same dedup registry -> should skip
    assert s3b.puts == {}


def test_reprocessing_same_email_with_attachment_skips_attachment_outputs():
    key = "emails/2026-07-27/att.eml"
    dedup = FakeDedup()

    s3a = FakeS3({key: read_fixture("with_attachment.eml")})
    _run(key, s3a, dedup)

    s3b = FakeS3({key: read_fixture("with_attachment.eml")})
    _run(key, s3b, dedup)
    assert s3b.puts == {}


def test_attachment_is_extracted_and_inline_skipped():
    key = "emails/2026-07-27/att.eml"
    s3 = FakeS3({key: read_fixture("with_attachment.eml")})
    _run(key, s3, FakeDedup())

    attachment_md = [
        k for k in s3.puts if k.startswith("emails-extracted/attachments/") and k.endswith(".md")
    ]
    assert len(attachment_md) == 1
    assert "report" in attachment_md[0]
    assert "Line one of report" in s3.puts[attachment_md[0]]


# --- full handler integration via moto --------------------------------------

@pytest.fixture
def aws_environment(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("SOURCE_BUCKET", BUCKET)
    monkeypatch.setenv("DEDUP_TABLE", TABLE)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield s3


def test_handler_end_to_end(aws_environment):
    s3 = aws_environment
    source_key = "emails/2026-07-27/sample.eml"
    s3.put_object(Bucket=BUCKET, Key=source_key, Body=read_fixture("forward_plain.eml"))

    s3_event = {
        "Records": [
            {"s3": {"bucket": {"name": BUCKET}, "object": {"key": source_key}}}
        ]
    }
    event = {"Records": [{"messageId": "m1", "body": json.dumps(s3_event)}]}

    response = handler(event, None)
    assert response == {"batchItemFailures": []}

    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix="emails-extracted/content/")
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    md_keys = [k for k in keys if k.endswith(".md")]
    assert len(md_keys) == 2


def test_handler_reports_failure_for_missing_object(aws_environment):
    event = {
        "Records": [
            {
                "messageId": "m-bad",
                "body": json.dumps(
                    {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": "emails/2026-07-27/missing.eml"}}}]}
                ),
            }
        ]
    }
    response = handler(event, None)
    assert response == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}
