# Deployed checks, 2026-10-16

Stack: dev, `us-east-1`, main at 9004871, reserved concurrency on (worker 10,
api 5), fault injection off. Client: a us-east cloud container calling the
Function URL with real Qrypt entropy. Job ids expire with the 7-day TTL
(around 2026-10-23).

## 1M-path job and replay

| | Value |
|---|---|
| Original job | `cd04f78dc20a13368a12064a2ca18b7c`: 1,000,000 paths, 10 shards, S0=100 K=100 r=0.05 σ=0.2 T=1 |
| Status | `COMPLETE`, submit 17:38:02.93Z → complete 17:38:06.16Z (3.2 s) |
| Estimate | 10.431386915594596 (`0x1.4dcdebeeccd5dp+3`) |
| Standard error | 0.014711551257944136 (`0x1.e2116fc35db95p-7`) |
| 95% CI | 10.40255280497231 to 10.460221026216882 |
| Black-Scholes | 10.450583572185565; z = −1.30 (within 3 SE) |
| Entropy | `entropy_source: "qrypt"`, `endpoint_host: api-eus.qrypt.com` |
| Replay job | `aa42392e5e6442b5f61314139455090b` (`replay_of` the job above), `COMPLETE` |
| Replay verdict | `bit-identical`: estimate, SE, CI low and CI high hex all equal |

The original's shards ran on two CPU-feature sets (`runtime_consistent: false`),
and the replay still matched on all four float64 hex values, as on 2026-10-14.

## Benchmarks

- Submit latency, 50 submissions 1 s apart:
  `results-latency-20261016T173906Z.json`. 48 accepted; p95 623.6 ms, p50
  353.2 ms. The other two were Qrypt failures, rejected fail-closed with
  `entropy_unavailable`: one 503, and one 504 `qrypt_timeout` after 5.7 s. With
  those two included, the warm p95 is 631.4 ms.
- 1M paths, submit to `COMPLETE`, 5 runs: `results-wallclock-20261016T174021Z.json`.
  Median 0.58 s, max 1.12 s; all `COMPLETE` and within 3 SE (worst z = 1.39).

## Live probes (no entropy spent)

401 without a key or with a wrong key; 400 with every problem listed for
invalid bodies; 404 with a message for an unknown id; `fail_shards` rejected
while fault injection is off; `/system` reports DLQ depth 0 after the purge.

Found by these probes and fixed afterwards: a 400-digit `S0` returned 500
instead of 400.
