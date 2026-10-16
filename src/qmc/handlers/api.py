"""API Lambda behind a Function URL (AuthType NONE + application-layer key check).

Routes
  GET  /                      web console (static, no auth)
  GET  /health                configuration + last-success times (never decrypts, never calls Qrypt)
  GET  /system                limits, entropy ledger, DLQ depth
  GET  /jobs                  list, newest first (?status=&limit=&before=)
  POST /jobs                  validate, rate-limit, fetch Qrypt entropy, create, enqueue
  GET  /jobs/{id}             job record with shard progress
  GET  /jobs/{id}/bundle      replay bundle for offline verification (verify.py)
  POST /jobs/{id}/replay      recompute from the stored seed (no new entropy)

Every route except GET / requires `Authorization: Bearer <key>`, where the key
is the SSM SecureString named by API_KEY_PARAM. Nothing here logs the event,
the body or any header.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
from functools import cached_property
from pathlib import Path

import boto3

from .. import ids, log, metrics
from ..jobs import DEADLINE_S, create_and_enqueue, new_job_record, params_from_record
from ..params import Limits, ValidationError, validate_job_request
from ..qrypt import EntropySource, QryptClient, QryptError
from ..runtime import expected_fingerprint, fingerprint_mismatch
from ..seed import SEED_DERIVATION, block_sha256, master_seed_from_block
from ..store import ALL_STATUSES, Conflict, Store

MAX_BODY_BYTES = 4096
RATE_WINDOW_S = 10
CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"

SECURITY_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class HttpError(Exception):
    def __init__(self, status: int, error: str, message: str, headers: dict | None = None, **extra):
        super().__init__(message)
        self.status = status
        self.body = {"error": error, "message": message, **extra}
        self.headers = headers or {}


def _json(status: int, body, headers: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **SECURITY_HEADERS, **(headers or {})},
        "body": json.dumps(body, default=str),
    }


def _head(method: str, resp: dict) -> dict:
    """HEAD gets GET's status and headers without the body (link checkers use it)."""
    return {**resp, "body": ""} if method == "HEAD" else resp


class SecretCache:
    """Loads SSM SecureStrings, each one KMS decrypt, and keeps them for ttl_s.

    The TTL bounds how long a rotated key keeps working in a warm container
    (and how long a new one is refused) while keeping KMS decrypts far under the
    free 20,000/month: at most 2 per container per hour. Surrounding whitespace,
    such as the newline an editor or `print()` leaves in the file the parameter
    was created from, is stripped; it is never part of a key or token.
    """

    def __init__(self, ssm_client_factory, ttl_s: float = 3600.0, clock=time.monotonic):
        self._factory = ssm_client_factory
        self._ttl_s = ttl_s
        self._clock = clock
        self._values: dict[str, tuple[str, float]] = {}
        self.loaded_at: dict[str, str] = {}

    def get(self, name: str) -> str:
        cached = self._values.get(name)
        if cached is None or self._clock() - cached[1] >= self._ttl_s:
            resp = self._factory().get_parameter(Name=name, WithDecryption=True)
            self._values[name] = (resp["Parameter"]["Value"].strip(), self._clock())
            self.loaded_at[name] = ids.iso(ids.now())
        return self._values[name][0]

    def reachable(self, name: str) -> bool:
        """SSM reachability without a decrypt: metadata only, so no KMS request."""
        try:
            self._factory().get_parameter(Name=name, WithDecryption=False)
            return True
        except Exception:
            return False


class App:
    """Holds per-container state. Tests construct it with a fake entropy source."""

    def __init__(
        self,
        env: dict | None = None,
        store: Store | None = None,
        entropy: EntropySource | None = None,
        secrets: SecretCache | None = None,
    ):
        self.env = dict(os.environ if env is None else env)
        self.limits = Limits.from_env(self.env)
        self._store = store
        self.secrets = secrets or SecretCache(
            lambda: boto3.client("ssm"), ttl_s=float(self.env.get("SECRET_TTL_S", "3600"))
        )
        self._entropy = entropy
        self.last_success: dict[str, str | None] = {"qrypt_fetch": None, "job_created": None}

    @cached_property
    def store(self) -> Store:
        return self._store or Store(self.env)

    @property
    def entropy(self) -> EntropySource:
        if self._entropy is None:
            self._entropy = QryptClient(
                token_provider=lambda: self.secrets.get(self.env["QRYPT_KEY_PARAM"]),
                url=self.env.get("QRYPT_ENTROPY_URL", "https://api-eus.qrypt.com/api/v1/quantum-entropy"),
                timeout_s=float(self.env.get("QRYPT_TIMEOUT_S", "5")),
            )
        return self._entropy

    @property
    def rate_limit(self) -> int:
        return int(self.env.get("QRYPT_RATE_PER_10S", "14"))

    @property
    def job_deadline_s(self) -> int:
        return int(self.env.get("JOB_DEADLINE_S", str(DEADLINE_S)))

    @property
    def replay_limit_per_day(self) -> int:
        return int(self.env.get("REPLAY_PER_DAY", "100"))

    @property
    def entropy_ceiling(self) -> int:
        return int(self.env.get("ENTROPY_CEILING_KIB", "5000"))

    # ---- request plumbing -------------------------------------------------

    def handle(self, event: dict, context) -> dict:
        started = time.perf_counter()
        http = (event.get("requestContext") or {}).get("http") or {}
        method = http.get("method", "GET").upper()
        path = event.get("rawPath") or "/"
        request_id = getattr(context, "aws_request_id", None)
        try:
            resp = self.route(method, path, event)
        except HttpError as exc:
            resp = _json(exc.status, exc.body, exc.headers)
        except Exception:
            log.error(event="unhandled", request_id=request_id, method=method, path=_path_label(path))
            resp = _json(500, {"error": "internal_error", "message": "unexpected server error"})
        log.info(
            event="request",
            request_id=request_id,
            method=method,
            path=_path_label(path),
            status=resp["statusCode"],
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return resp

    def authorize(self, event: dict) -> None:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        supplied = headers.get("authorization", "")
        if not supplied.startswith("Bearer "):
            raise HttpError(401, "unauthorized", "missing bearer key")
        try:
            expected = self.secrets.get(self.env["API_KEY_PARAM"])
        except Exception:
            log.error(event="api_key_unavailable")
            raise HttpError(503, "api_key_unavailable", "API key could not be loaded") from None
        if not hmac.compare_digest(supplied[7:].encode(), expected.encode()):
            raise HttpError(401, "unauthorized", "invalid bearer key")

    def body_json(self, event: dict):
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            if len(raw) > MAX_BODY_BYTES * 4 // 3 + 4:
                raise HttpError(413, "body_too_large", f"body must be <= {MAX_BODY_BYTES} bytes")
            raw = base64.b64decode(raw).decode("utf-8", errors="replace")
        if len(raw.encode()) > MAX_BODY_BYTES:
            raise HttpError(413, "body_too_large", f"body must be <= {MAX_BODY_BYTES} bytes")
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            raise HttpError(400, "invalid_json", "body is not valid JSON") from None

    def route(self, method: str, path: str, event: dict) -> dict:
        path = path.rstrip("/") or "/"
        if method in ("GET", "HEAD") and path in ("/", "/index.html"):
            return _head(method, self.console(event))
        if method in ("GET", "HEAD") and path.startswith("/static/"):
            return _head(method, self.static(path))
        self.authorize(event)
        if path == "/health" and method == "GET":
            return self.health()
        if path == "/system" and method == "GET":
            return self.system()
        if path == "/jobs":
            if method == "GET":
                return self.list_jobs(event)
            if method == "POST":
                return self.submit(event)
        m = re.fullmatch(r"/jobs/([^/]+)(/replay|/bundle)?", path)
        if m:
            job_id, suffix = m.group(1), m.group(2)
            if not ids.is_job_id(job_id):
                raise HttpError(400, "invalid_job_id", "job id must be 32 lowercase hex characters")
            if suffix is None and method == "GET":
                return self.get_job(job_id)
            if suffix == "/replay" and method == "POST":
                return self.replay(job_id)
            if suffix == "/bundle" and method == "GET":
                return self.bundle(job_id)
        raise HttpError(404, "not_found", f"no route for {method} {_path_label(path)}")

    # ---- routes -----------------------------------------------------------

    def console(self, event: dict) -> dict:
        return self.static("/static/index.html")

    def static(self, path: str) -> dict:
        name = path.removeprefix("/static/")
        types = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript"}
        target = (CONSOLE_DIR / name).resolve()
        if CONSOLE_DIR not in target.parents or target.suffix not in types or not target.is_file():
            raise HttpError(404, "not_found", "no such file")
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": types[target.suffix],
                **SECURITY_HEADERS,
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
                ),
            },
            "body": target.read_text(),
        }

    def health(self) -> dict:
        tables = self.store.describe_tables()
        # SSM reachability: parameter metadata only (WithDecryption=false), so
        # health never spends a KMS request or touches a secret value.
        ssm_ok = self.secrets.reachable(self.env.get("API_KEY_PARAM", ""))
        ok = all(v == "ACTIVE" for v in tables.values()) and ssm_ok
        return _json(
            200 if ok else 503,
            {
                "status": "ok" if ok else "degraded",
                "environment": self.env.get("ENVIRONMENT"),
                "tables": tables,
                "ssm": "reachable" if ssm_ok else "unreachable",
                "config": {
                    "qrypt_key_param_configured": bool(self.env.get("QRYPT_KEY_PARAM")),
                    "api_key_param_configured": bool(self.env.get("API_KEY_PARAM")),
                    "queue_configured": bool(self.env.get("SHARD_QUEUE_URL")),
                },
                "last_success": {
                    "api_key_loaded_at": self.secrets.loaded_at.get(self.env.get("API_KEY_PARAM", "")),
                    "qrypt_key_loaded_at": self.secrets.loaded_at.get(self.env.get("QRYPT_KEY_PARAM", "")),
                    "qrypt_fetch_at": self.last_success["qrypt_fetch"],
                    "job_created_at": self.last_success["job_created"],
                },
                "scope": "per-container; a cold container shows nulls until its first request of each kind",
            },
        )

    def system(self) -> dict:
        dlq_depth = None
        dlq_url = self.env.get("DLQ_URL")
        if dlq_url:
            try:
                attrs = self.store.sqs.get_queue_attributes(
                    QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
                )["Attributes"]
                dlq_depth = int(attrs["ApproximateNumberOfMessages"])
            except Exception:
                dlq_depth = None
        return _json(
            200,
            {
                "limits": self.limits.as_dict(),
                "entropy": {
                    "kib_used": self.store.entropy_used(),
                    "ceiling_kib": self.entropy_ceiling,
                    "kib_per_job": 1,
                },
                "qrypt_rate_limit": {"requests": self.rate_limit, "window_s": RATE_WINDOW_S},
                "dlq_depth": dlq_depth,
                "runtime": expected_fingerprint(),
                "auth": "application-layer bearer key check on a public Function URL",
            },
        )

    def list_jobs(self, event: dict) -> dict:
        q = event.get("queryStringParameters") or {}
        status = q.get("status")
        if status is not None and status not in ALL_STATUSES:
            raise HttpError(400, "invalid_status", f"status must be one of {', '.join(ALL_STATUSES)}")
        try:
            limit = int(q.get("limit", "25"))
        except ValueError:
            raise HttpError(400, "invalid_limit", "limit must be an integer") from None
        if not 1 <= limit <= 100:
            raise HttpError(400, "invalid_limit", "limit must be between 1 and 100")
        before = q.get("before")
        jobs = self.store.list_jobs(status, limit, before)
        items = [summary(j) for j in jobs]
        next_cursor = items[-1]["created_at"] if len(items) == limit else None
        return _json(200, {"jobs": items, "next_before": next_cursor})

    def get_job(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if not job:
            raise HttpError(404, "job_not_found", f"no job with id {job_id}")
        return _json(200, present(job))

    def submit(self, event: dict) -> dict:
        body = self.body_json(event)
        try:
            params, fail_shards = validate_job_request(body, self.limits)
        except ValidationError as exc:
            raise HttpError(400, "invalid_request", str(exc), problems=exc.problems) from None

        # Our own limiter keeps bursts under Qrypt's 30 requests / 10 s.
        now = ids.now()
        window = int(now.timestamp()) // RATE_WINDOW_S
        if not self.store.take_rate_token(str(window), self.rate_limit, (window + 2) * RATE_WINDOW_S + 60):
            retry_after = RATE_WINDOW_S - int(now.timestamp()) % RATE_WINDOW_S
            raise HttpError(
                429,
                "rate_limited",
                "too many submissions; Qrypt allows 30 requests per 10 seconds",
                headers={"Retry-After": str(retry_after)},
                retry_after_s=retry_after,
                job_created=False,
            )
        try:
            used = self.store.reserve_entropy(1, self.entropy_ceiling)
        except Conflict:
            raise HttpError(
                409,
                "entropy_ceiling_reached",
                f"entropy ceiling of {self.entropy_ceiling} KiB reached; no job created",
                job_created=False,
            ) from None

        # Fail closed: Qrypt is the only entropy source. Any failure rejects the job.
        try:
            block = self.entropy.fetch_block()
        except QryptError as exc:
            metrics.emit(QryptFailures=1)
            log.warn(event="qrypt_failure", cause=exc.cause, http_status=exc.http_status)
            status = 504 if exc.cause == "qrypt_timeout" else 503
            raise HttpError(
                status,
                "entropy_unavailable",
                exc.message,
                cause=exc.cause,
                qrypt_status=exc.http_status,
                job_created=False,
            ) from None

        fetched = ids.now()
        self.last_success["qrypt_fetch"] = ids.iso(fetched)
        master = master_seed_from_block(block.data)
        block_hash = block_sha256(block.data)
        entropy = {
            "source": "qrypt",
            "endpoint_host": block.endpoint_host,
            "qrypt_request_id": block.request_id,
            "block_sha256": block_hash,
            "kib": block.kib,
            "fetched_at": ids.iso(fetched),
            "derivation": SEED_DERIVATION,
            "ledger_kib_after": used,
        }

        def make(job_id: str) -> dict:
            return new_job_record(
                job_id=job_id,
                params=params,
                master_seed_hex=master,
                entropy=entropy,
                created=fetched,
                fail_shards=fail_shards,
                deadline_s=self.job_deadline_s,
            )

        job = create_and_enqueue(self.store, make, (block_hash, ids.iso(fetched)))
        self.last_success["job_created"] = ids.iso(ids.now())
        metrics.emit(JobsSubmitted=1, EntropyKiBConsumed=block.kib)
        log.info(event="job_submitted", job_id=job["job_id"], paths=params.paths, shards=params.shards)
        return _json(
            202,
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "master_seed_hex": master,
                "entropy_source": "qrypt",
                "links": _links(job["job_id"]),
            },
        )

    def replay(self, job_id: str) -> dict:
        original = self.store.get_job(job_id)
        if not original:
            raise HttpError(404, "job_not_found", f"no job with id {job_id}")
        if original["status"] != "COMPLETE":
            raise HttpError(
                409, "not_replayable", f"only COMPLETE jobs can be replayed (status is {original['status']})"
            )
        mismatch = fingerprint_mismatch(original.get("runtime_expected", {}), expected_fingerprint())
        if mismatch:
            raise HttpError(
                409,
                "runtime_mismatch",
                "the deployed runtime differs from the one that produced this job, "
                "so a replay could not be bit-identical",
                fields=mismatch,
                recorded=original.get("runtime_expected"),
                current=expected_fingerprint(),
            )
        # Replay spends no entropy, so the Qrypt limits do not bound it; a daily
        # cap does, so a key holder cannot loop replays of a large job.
        today = ids.now()
        if not self.store.take_rate_token(
            f"replay-{today:%Y-%m-%d}", self.replay_limit_per_day, ids.epoch_s(ids.plus(today, 2 * 86400))
        ):
            raise HttpError(
                429,
                "replay_limit_reached",
                f"at most {self.replay_limit_per_day} replays per UTC day; try again tomorrow",
                job_created=False,
            )
        params = params_from_record(original)
        created = ids.now()
        # Replay reuses the stored seed and its provenance: no Qrypt call, no new entropy.
        entropy = dict(original["entropy"])

        def make(new_id: str) -> dict:
            return new_job_record(
                job_id=new_id,
                params=params,
                master_seed_hex=original["master_seed_hex"],
                entropy=entropy,
                created=created,
                replay_of=job_id,
                deadline_s=self.job_deadline_s,
            )

        job = create_and_enqueue(self.store, make, (job_id, "replay", ids.iso(created)))
        log.info(event="replay_submitted", job_id=job["job_id"], replay_of=job_id)
        return _json(
            202,
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "replay_of": job_id,
                "entropy_used_kib": 0,
                "links": _links(job["job_id"]),
            },
        )

    def bundle(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if not job:
            raise HttpError(404, "job_not_found", f"no job with id {job_id}")
        return _json(
            200,
            {
                "bundle_version": 1,
                "job_id": job_id,
                "params": job["params"],
                "master_seed_hex": job["master_seed_hex"],
                "entropy": job["entropy"],
                "runtime_expected": job.get("runtime_expected"),
                "result": job.get("result"),
                "status": job["status"],
                "verify": "python scripts/verify.py bundle.json",
            },
            headers={"Content-Disposition": f'attachment; filename="qmc-{job_id}.json"'},
        )


def _links(job_id: str) -> dict:
    return {"self": f"/jobs/{job_id}", "replay": f"/jobs/{job_id}/replay", "bundle": f"/jobs/{job_id}/bundle"}


def _path_label(path: str) -> str:
    """Path for logs, with any job id collapsed so logs never grow unbounded labels."""
    return re.sub(r"/jobs/[^/]+", "/jobs/{id}", path)[:64]


def summary(job: dict) -> dict:
    p = job.get("params", {})
    res = job.get("result") or {}
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "completed_at": job.get("completed_at"),
        "paths": p.get("paths"),
        "shards": p.get("shards"),
        "estimate": res.get("estimate"),
        "replay_of": job.get("replay_of"),
    }


def present(job: dict) -> dict:
    shards = int(job["params"]["shards"])
    done = job.get("done_shards", [])
    failed = sorted(set(job.get("failed_shards", [])) - set(done))
    out = {k: v for k, v in job.items() if k not in ("done_shards", "failed_shards", "expires_at")}
    out["progress"] = {
        "shards": shards,
        "done": len(done),
        "failed": len(failed),
        "done_indexes": done,
        "failed_indexes": failed,
    }
    out["links"] = _links(job["job_id"])
    return out


_APP: App | None = None


def handler(event, context):
    global _APP
    if _APP is None:
        _APP = App()
    return _APP.handle(event, context)
