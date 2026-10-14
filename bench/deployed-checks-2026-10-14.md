# Deployed checks, 2026-10-14

Stack `qmc-dev` (us-east-1), applied from commit 48d2aac and redeployed
~22:02:50Z by the build thread, with reserved
concurrency off (see README "Known constraints"). Run from a cloud session
against the Function URL with the real bearer key. Nothing stubbed; every
value below is copied from the live responses.

| # | Check | Result |
|---|---|---|
| 1 | `qmc_cli.py health` | PASS: both tables ACTIVE, all three config flags true, SSM key loads recorded |
| 1 | `qmc_cli.py system` | PASS: numpy 2.5.4, arm64, caps 10M paths / 100 shards, fault injection off, DLQ depth 0 |
| 2 | 1M-path Black-Scholes job | BLOCKED: Qrypt answered 403, job rejected (below) |
| 3 | Replay | BLOCKED: no job exists to replay |
| 4 | Bundle + offline verify | BLOCKED: no job exists |
| 5 | Latency / wall-clock benchmarks | NOT RUN: every submit is rejected, so a latency figure would measure the rejection path, not the target |
| 6 | Fail-closed (`deployed_failclosed_test.py`) | NOT RUN (needs Paawan's OK). A real upstream rejection was observed instead, see below |
| 7 | DLQ alarm | NOT RUN (needs an apply with fault injection) |

## Observed fail-closed rejection (real Qrypt response, not injected)

Three submissions: ~21:59Z and 22:00:46Z on the 48d2aac code (1M paths / 10
shards, then 1,000 paths / 1 shard), and 22:09:37Z (1M / 10) after the
redeploy at ~22:02:50Z. All three gave the same answer:

```
http_status 503, error entropy_unavailable, cause qrypt_allotment_exhausted,
qrypt_status 403, job_created false   (client-side latency 278, 641 and 475 ms)
```

`list` afterwards returned no jobs: no job record was created and no local
seed was used. `system` reported `kib_used` 3 of the 5,000 KiB ceiling after
the second and the third attempts.

## Open question on the 403

The client maps Qrypt 403 to "allotment used up", but these were the first
entropy requests this deployment ever made (`jobs` was empty), so the 403 more
likely means the token is not entitled to the Entropy product, the trial is not
active, or the token is wrong. Not verified: the token is never read from SSM
in this session and the Qrypt host is not reachable from it.
