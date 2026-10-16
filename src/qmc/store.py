"""DynamoDB and SQS access. Thin wrappers; all decisions live in the callers.

Tables
- jobs:   PK job_id (S). GSI "by_status": status (S) + created_at (S).
          Also holds counter items whose ids start with "#": one per Qrypt
          rate-limit window, one per replay day, and the entropy ledger. They
          have no status, so they never appear in the GSI, and the API's job-id
          format can't address them.
- shards: PK job_id (S) + shard_index (N). Conditional put makes a redelivered
          shard a no-op.

Numbers that must survive bit-for-bit are stored as float.hex() strings.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from decimal import Decimal
from functools import cached_property

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

from . import ids

FINAL_STATUSES = ("COMPLETE", "PARTIAL")
ACTIVE_STATUSES = ("QUEUED", "RUNNING", "AGGREGATING")
ALL_STATUSES = ACTIVE_STATUSES + FINAL_STATUSES
STATUS_INDEX = "by_status"
ENTROPY_LEDGER_ID = "#entropy-ledger"
TTL_DAYS = 7

_BOTO_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"}, connect_timeout=3, read_timeout=10)


class Conflict(Exception):
    """A conditional write lost (already exists / wrong state / over a limit)."""


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def to_dynamo(value):
    """Convert floats to Decimal via repr (exact round trip) for DynamoDB."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value):
    """Decimals back to int/float; number sets to sorted int lists."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(str(value))
    if isinstance(value, set):
        return sorted(int(v) for v in value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


class Store:
    def __init__(self, env: Mapping[str, str] | None = None, session=None):
        src: Mapping[str, str] = os.environ if env is None else env
        self.jobs_table_name = src["JOBS_TABLE"]
        self.shards_table_name = src["SHARDS_TABLE"]
        self.queue_url = src.get("SHARD_QUEUE_URL", "")
        self._session = session or boto3.session.Session()

    @cached_property
    def _ddb(self):
        return self._session.resource("dynamodb", config=_BOTO_CONFIG)

    @cached_property
    def ddb_client(self):
        return self._session.client("dynamodb", config=_BOTO_CONFIG)

    @cached_property
    def sqs(self):
        return self._session.client("sqs", config=_BOTO_CONFIG)

    @cached_property
    def jobs(self):
        return self._ddb.Table(self.jobs_table_name)

    @cached_property
    def shards(self):
        return self._ddb.Table(self.shards_table_name)

    # ---- jobs -------------------------------------------------------------

    def put_new_job(self, item: dict) -> None:
        try:
            self.jobs.put_item(Item=to_dynamo(item), ConditionExpression="attribute_not_exists(job_id)")
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise Conflict("job id exists") from None
            raise

    def get_job(self, job_id: str, consistent: bool = True) -> dict | None:
        resp = self.jobs.get_item(Key={"job_id": job_id}, ConsistentRead=consistent)
        item = resp.get("Item")
        return from_dynamo(item) if item else None

    def list_jobs(self, status: str | None, limit: int, before: str | None) -> list[dict]:
        """Newest first. Queries the status GSI (one partition per status) and merges."""
        statuses = [status] if status else list(ALL_STATUSES)
        out: list[dict] = []
        for st in statuses:
            cond = Key("status").eq(st)
            if before:
                cond = cond & Key("created_at").lt(before)
            resp = self.jobs.query(
                IndexName=STATUS_INDEX,
                KeyConditionExpression=cond,
                ScanIndexForward=False,
                Limit=limit,
            )
            out.extend(from_dynamo(i) for i in resp.get("Items", []))
        out.sort(key=lambda j: j["created_at"], reverse=True)
        return out[:limit]

    def active_jobs_past_deadline(self, now_iso: str) -> list[dict]:
        out: list[dict] = []
        for st in ACTIVE_STATUSES:
            kwargs = {
                "IndexName": STATUS_INDEX,
                "KeyConditionExpression": Key("status").eq(st),
                "FilterExpression": Attr("deadline_at").lt(now_iso),
            }
            while True:
                resp = self.jobs.query(**kwargs)
                out.extend(from_dynamo(i) for i in resp.get("Items", []))
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return out

    def mark_shard_done(self, job_id: str, shard_index: int) -> dict:
        """Idempotent: adding to a number set twice is a no-op."""
        resp = self.jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="ADD done_shards :s SET updated_at = :t",
            ConditionExpression="attribute_exists(job_id)",
            ExpressionAttributeValues={":s": {shard_index}, ":t": ids.iso(ids.now())},
            ReturnValues="ALL_NEW",
        )
        return from_dynamo(resp["Attributes"])

    def mark_shard_failed(self, job_id: str, shard_index: int, code: str) -> dict:
        resp = self.jobs.update_item(
            Key={"job_id": job_id},
            UpdateExpression="ADD failed_shards :s SET updated_at = :t, shard_errors.#i = :c",
            ConditionExpression="attribute_exists(job_id)",
            ExpressionAttributeNames={"#i": str(shard_index)},
            ExpressionAttributeValues={":s": {shard_index}, ":t": ids.iso(ids.now()), ":c": code},
            ReturnValues="ALL_NEW",
        )
        return from_dynamo(resp["Attributes"])

    def transition(
        self, job_id: str, from_statuses: tuple[str, ...], to_status: str, extra: dict | None = None
    ) -> bool:
        """Conditionally move a job between statuses. False if the condition lost."""
        names = {"#s": "status"}
        values = {":to": to_status, ":t": ids.iso(ids.now())}
        sets = ["#s = :to", "updated_at = :t"]
        for i, (k, v) in enumerate((extra or {}).items()):
            names[f"#e{i}"] = k
            values[f":e{i}"] = to_dynamo(v)
            sets.append(f"#e{i} = :e{i}")
        from_keys = []
        for i, st in enumerate(from_statuses):
            values[f":f{i}"] = st
            from_keys.append(f":f{i}")
        try:
            self.jobs.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=f"#s IN ({', '.join(from_keys)})",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    # ---- shards -----------------------------------------------------------

    def put_shard(self, item: dict) -> bool:
        """Conditional put on job_id + shard_index. False if already written."""
        try:
            self.shards.put_item(
                Item=to_dynamo(item),
                ConditionExpression="attribute_not_exists(shard_index)",
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def get_shards(self, job_id: str) -> list[dict]:
        kwargs = {"KeyConditionExpression": Key("job_id").eq(job_id), "ConsistentRead": True}
        out: list[dict] = []
        while True:
            resp = self.shards.query(**kwargs)
            out.extend(from_dynamo(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return out

    # ---- counters (rate limit and entropy ledger) ---------------------------

    def take_rate_token(self, window_id: str, limit: int, ttl_epoch: int) -> bool:
        try:
            self.jobs.update_item(
                Key={"job_id": f"#qrypt-rate#{window_id}"},
                UpdateExpression="ADD hits :one SET expires_at = :ttl",
                ConditionExpression="attribute_not_exists(hits) OR hits < :limit",
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":ttl": ttl_epoch},
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def reserve_entropy(self, kib: int, ceiling_kib: int) -> int:
        """Count KiB against the ceiling before the Qrypt call. Raises Conflict if over."""
        try:
            resp = self.jobs.update_item(
                Key={"job_id": ENTROPY_LEDGER_ID},
                UpdateExpression="ADD kib_used :k, requests :one",
                ConditionExpression="attribute_not_exists(kib_used) OR kib_used <= :max",
                ExpressionAttributeValues={":k": kib, ":one": 1, ":max": ceiling_kib - kib},
                ReturnValues="UPDATED_NEW",
            )
            return int(resp["Attributes"]["kib_used"])
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise Conflict("entropy ceiling reached") from None
            raise

    def entropy_used(self) -> int:
        item = self.jobs.get_item(Key={"job_id": ENTROPY_LEDGER_ID}).get("Item") or {}
        return int(item.get("kib_used", 0))

    # ---- queue ------------------------------------------------------------

    def enqueue_shards(self, messages: list[dict]) -> list[int]:
        """SendMessageBatch in groups of 10. Returns shard indexes that failed twice."""
        failed: list[int] = []
        for start in range(0, len(messages), 10):
            batch = messages[start : start + 10]
            entries = {str(m["shard_index"]): m for m in batch}
            for _attempt in range(2):
                resp = self.sqs.send_message_batch(
                    QueueUrl=self.queue_url,
                    Entries=[{"Id": k, "MessageBody": json.dumps(v)} for k, v in entries.items()],
                )
                entries = {f["Id"]: entries[f["Id"]] for f in resp.get("Failed", [])}
                if not entries:
                    break
            failed.extend(int(k) for k in entries)
        return failed

    def describe_tables(self) -> dict:
        out = {}
        for name in (self.jobs_table_name, self.shards_table_name):
            try:
                out[name] = self.ddb_client.describe_table(TableName=name)["Table"]["TableStatus"]
            except ClientError as exc:
                out[name] = f"error:{exc.response.get('Error', {}).get('Code', 'unknown')}"
        return out
