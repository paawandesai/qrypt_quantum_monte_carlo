# Deployed checks after MR !9, 2026-10-16

Stack: dev, `us-east-1`, main at 7978c46 (MR !9), reserved concurrency on
(worker 10, api 5). Applied 21:54Z: 9 resources changed in place, a second plan
showed no drift, the Function URL is unchanged. Client: a us-east cloud
container calling the Function URL with real Qrypt entropy. Job ids expire with
the 7-day TTL (around 2026-10-23).

## Probes (no entropy spent)

| Probe | Result |
|---|---|
| `HEAD /` | 200 |
| `GET /health` | 200, `"ssm": "reachable"` |
| `GET /jobs` without a key | 401 |
| `GET /jobs/foo` | 400 `invalid_job_id` |
| `POST /jobs` with a 400-digit `S0` (number and string) | 400 |
| `GET /system` before the rehearsal | DLQ depth 0 |

## 1M-path job and replay

| | Value |
|---|---|
| Original job | `856d4e305e3e213619bf221a1f7ca147`: 1,000,000 paths, 10 shards, S0=100 K=100 r=0.05 σ=0.2 T=1 |
| Status | `COMPLETE`, created 21:55:20.38Z → complete 21:55:23.73Z (3.4 s) |
| Estimate | 10.434992329492898 (`0x1.4deb7508a5d9fp+3`) |
| 95% CI | 10.406168874009584 to 10.463815784976212 |
| Black-Scholes | 10.450583572185565; z = −1.06 (within 3 SE) |
| Entropy | `endpoint_host: api-eus.qrypt.com`; `runtime_consistent: true` |
| Replay job | `d1131a387086dd263977df7cbbcfab55`, verdict `bit-identical` |

## Fault-injection rehearsal (5 receives, new alarm settings)

Fault injection turned on at 21:56:01Z (2 in-place changes), job
`ef5e8111cb8633dabae1f6e9125bae4b` (1,000 paths, 2 shards, shard 0 failing)
submitted at 21:56:35Z, fault injection turned off again at about 22:00Z
(2 in-place changes, second plan no drift).

| Event | Time | After submit |
|---|---|---|
| `worker-errors` alarm → `ALARM` | 21:57:46.6Z | 71 s |
| Job `PARTIAL` | between 21:58:12Z and 21:58:24Z | ~109 s |
| `shard-failures` alarm → `ALARM` | 21:59:45.0Z | 189 s |
| Message in the DLQ (depth 1 via `/system`) | ~21:59:34Z | ~179 s |
| `dlq-depth` alarm → `ALARM` | 22:02:26.5Z | 351 s |

Alarm states were read with `aws cloudwatch describe-alarms`. `api-errors` and
`qrypt-failures` stayed `OK`, as expected: no API error and no Qrypt failure
happened. The deploy user cannot read alarm history or SNS delivery metrics, so
whether the emails arrived is recorded by the candidate below.

Alarm emails, as received in the candidate's inbox (screenshot reviewed, not
committed): `ALARM` for `shard-failures` and `dlq-depth`, then `OK` for
`worker-errors` and `shard-failures` once their 3-minute windows cleared. The
`OK` for `worker-errors` follows its 21:57:46Z `ALARM` transition; that `ALARM`
email was not in the part of the inbox shown. This is the first rehearsal with
a confirmed email subscription (it was confirmed at 21:36Z the same day; the
earlier, unconfirmed one had been deleted by AWS).

The candidate purged the DLQ in the SQS console afterwards; `/system` reported
depth 0 at about 22:09Z.
