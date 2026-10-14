import json
import os

import boto3
import pytest
from moto import mock_aws

TEST_ENV = {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "JOBS_TABLE": "qmc-test-jobs",
    "SHARDS_TABLE": "qmc-test-shards",
    "API_KEY_PARAM": "/qmc-test/api-key",
    "QRYPT_KEY_PARAM": "/qmc-test/qrypt-api-key",
    "ENVIRONMENT": "test",
    "FUNCTION_ROLE": "test",
    "MAX_RECEIVE_COUNT": "3",
}

TEST_BEARER = "unit-test-bearer-value"


class FakeSecrets:
    def __init__(self, values=None):
        self.values = values if values is not None else {TEST_ENV["API_KEY_PARAM"]: TEST_BEARER}
        self.loaded_at = {}
        self.calls = 0

    def get(self, name):
        self.calls += 1
        if name not in self.values:
            raise KeyError(name)
        self.loaded_at[name] = "2026-01-01T00:00:00.000000Z"
        return self.values[name]


def create_resources() -> dict:
    """Create the tables and queues inside an active moto mock. Returns queue urls."""
    ddb = boto3.client("dynamodb")
    ddb.create_table(
        TableName=TEST_ENV["JOBS_TABLE"],
        AttributeDefinitions=[
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by_status",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    ddb.create_table(
        TableName=TEST_ENV["SHARDS_TABLE"],
        AttributeDefinitions=[
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "shard_index", "AttributeType": "N"},
        ],
        KeySchema=[
            {"AttributeName": "job_id", "KeyType": "HASH"},
            {"AttributeName": "shard_index", "KeyType": "RANGE"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 10, "WriteCapacityUnits": 10},
    )
    sqs = boto3.client("sqs")
    dlq = sqs.create_queue(QueueName="qmc-test-dlq")["QueueUrl"]
    q = sqs.create_queue(QueueName="qmc-test-shards")["QueueUrl"]
    os.environ["SHARD_QUEUE_URL"] = q
    os.environ["DLQ_URL"] = dlq
    return {"queue_url": q, "dlq_url": dlq, "sqs": sqs}


@pytest.fixture
def aws(monkeypatch):
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SHARD_QUEUE_URL", "")
    monkeypatch.setenv("DLQ_URL", "")
    with mock_aws():
        yield create_resources()


def fn_url_event(method, path, body=None, key=TEST_BEARER, query=None, headers=None):
    h = {"content-type": "application/json"}
    if key is not None:
        h["authorization"] = f"Bearer {key}"
    h.update(headers or {})
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": h,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None and not isinstance(body, str) else body,
        "isBase64Encoded": False,
    }


def drain_queue(sqs, queue_url, max_messages=1000):
    """Receive every message currently on the queue (as SQS event records)."""
    records = []
    while len(records) < max_messages:
        resp = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, AttributeNames=["ApproximateReceiveCount"]
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            records.append(
                {
                    "messageId": m["MessageId"],
                    "body": m["Body"],
                    "attributes": {"ApproximateReceiveCount": m["Attributes"]["ApproximateReceiveCount"]},
                }
            )
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return records


os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
