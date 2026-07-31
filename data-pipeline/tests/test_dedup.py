import boto3
import pytest
from moto import mock_aws

from email_pipeline.dedup import DedupRegistry, compute_identity

TABLE_NAME = "test-email-dedup"


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield resource


def test_identity_uses_message_id_when_present():
    identity = compute_identity("<abc@example.com>", "a", "b", "c", "body")
    assert identity == "<abc@example.com>"


def test_identity_falls_back_to_hash_when_no_message_id():
    a = compute_identity(None, "Alice", "date", "Subject", "Hello   world")
    b = compute_identity(None, "Alice", "date", "Subject", "Hello world")
    assert a.startswith("sha256:")
    # Whitespace-normalized bodies produce the same identity.
    assert a == b


def test_identity_differs_for_different_content():
    a = compute_identity(None, "Alice", "d", "S", "one")
    b = compute_identity(None, "Alice", "d", "S", "two")
    assert a != b


def test_claim_first_writer_wins(dynamodb_table):
    registry = DedupRegistry(TABLE_NAME, dynamodb_resource=dynamodb_table)
    assert registry.claim("id-1", {"source_key": "k"}) is True
    # Second claim of the same identity is rejected.
    assert registry.claim("id-1", {"source_key": "k2"}) is False


def test_claim_distinct_identities_both_succeed(dynamodb_table):
    registry = DedupRegistry(TABLE_NAME, dynamodb_resource=dynamodb_table)
    assert registry.claim("id-a") is True
    assert registry.claim("id-b") is True
