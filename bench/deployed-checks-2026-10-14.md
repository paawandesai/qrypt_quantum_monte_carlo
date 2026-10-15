# Deployed checks, 2026-10-14

Stack `qmc-dev` (us-east-1), reserved concurrency off (see README "Known
constraints"). Run from a cloud session against the Function URL with the real
bearer key. Nothing stubbed; values are copied from the live responses and the
committed `results-*.json` files.

| # | Check | Result |
|---|---|---|
| 1 | `qmc_cli.py health` / `system` | PASS: tables ACTIVE, config complete, numpy 2.5.4 on arm64, DLQ depth 0 |
| 2 | 1M-path Black-Scholes job | PASS: see below |
| 3 | Replay | PASS: `bit-identical` |
| 4 | Bundle + offline `verify.py` | PASS: `bit-identical` |
| 5 | Benchmarks | PASS: p95 619.8 ms (< 800), 1M median 1.07 s (< 60) |
| 6 | Fail-closed (`deployed_failclosed_test.py`) | PASS: 504 `qrypt_timeout`, no job created, config restored |
| 7 | DLQ alarm | PASS: real failing worker, job `PARTIAL`, both alarms fired (2026-10-15) |

## 2. The 1M-path job (23:04:51Z, after the User-Agent fix)

Job `f242eeb5cbc2ee5824464c2727063d89`, S0=100 K=100 r=0.05 sigma=0.2 T=1,
1,000,000 paths over 10 shards. `COMPLETE`, all 10 shards done.

| | value | float64 hex |
|---|---|---|
| estimate | 10.453435645040196 | 0x1.4e828b784939dp+3 |
| std error | 0.014718899828425936 | 0x1.e24f14b2b655ap-7 |
| 95% CI | [10.42458713148443, 10.482284158595963] | 0x1.4d9637c080e60p+3, 0x1.4f6edf30118dap+3 |
| Black-Scholes | 10.450583572185565 | |
| z | +0.194 (within 3 SE) | |

`entropy_source` = `qrypt`. `qrypt_request_id` is null: Qrypt's response
carried no request-id header or field, and the client stores null rather than
invent one. Provenance is the SHA-256 of the 1 KiB block
(`18155691…aecbcd`), stored on the job and in the bundle.

`runtime_consistent` is false: the shards ran on two hosts with different
CPU-feature hashes (same numpy, arch and algorithm). The results still matched
bit for bit on replay and offline.

## 3–4. Replay and offline verify

Replay job `c03364665af75b2997f0a1fb95fcdfd0` (0 KiB of new entropy) recomputed
all 10 shards: estimate, std error, CI low and CI high all equal as float64 hex,
verdict `bit-identical`. `verify.py` on the bundle, run on an x86_64 machine
(numpy 2.5.4), also gave `bit-identical` on all four values.

## 5. Benchmarks

- `results-latency-20261014T230524Z.json`: 50 of 50 submissions accepted;
  warm p50 325.1 ms, p95 619.8 ms, max 739.6 ms; first request 561.7 ms.
  Measured client-side from a cloud container in the US, through its proxy, so
  it includes that network path.
- `results-wallclock-20261014T230633Z.json`: 5 runs of 1M paths / 10 shards,
  submit to `COMPLETE`: median 1.07 s, max 1.4 s. All 5 within 3 SE of
  Black-Scholes (z from −2.07 to +0.65).

Entropy used after all checks: 60 KiB of the 5,000 KiB ceiling.

## 6. Fail-closed test (23:08:49Z–23:09:02Z, approved by Paawan)

`deployed_failclosed_test.py --function qmc-dev-api` pointed the API at a
non-routable Qrypt address, submitted, then restored the saved environment:

```
http_status 504, error entropy_unavailable, cause qrypt_timeout,
qrypt_status null, job_created false, new_jobs [], latency 6369 ms
PASS: rejected, no job created
```

Afterwards `QRYPT_ENTROPY_URL` read back as the default endpoint and a 1,000-path
job completed normally.

## 7. DLQ alarm (2026-10-15, approved by Paawan)

The build thread applied `enable_fault_injection=true` at 13:17Z. Job
`d452c4a791c519a5e602296d7ac23ba9` (20,000 paths, 2 shards, `fail_shards: [0]`)
was submitted at 13:18:43Z. Shard 0 raised `InjectedFault` in the real worker on
every delivery; nothing was published to SNS by hand.

| time (UTC) | event |
|---|---|
| 13:20:44 | job `PARTIAL`: shard 1 done, shard 0 failed (`InjectedFault`) |
| 13:21:42 | alarm `qmc-dev-shard-failures` → ALARM |
| ~13:22 | DLQ depth 1 (after `maxReceiveCount` = 3 deliveries) |
| 13:24:54 | alarm `qmc-dev-dlq-depth` → ALARM |

Submit to DLQ alarm: about 6 minutes. The job never reported `COMPLETE`. The DLQ is
never consumed, so its alarm stays in ALARM until the message expires or is purged.

## Earlier attempts, and the cause

Before 23:04Z every submission (21:59Z, 22:00Z, 22:09Z) was rejected with 503
`entropy_unavailable`, Qrypt status 403, `job_created: false`: the fail-closed
path working against a real upstream refusal. Paawan reproduced the cause from
a Mac: Qrypt is behind Cloudflare, which answers urllib's default User-Agent
(`Python-urllib/3.13`) with 403 "error code: 1010", while the same token with
curl's User-Agent got entropy. Commit bb71747 sends `qrypt-monte-carlo/0.1`
and was deployed at ~23:04Z.
