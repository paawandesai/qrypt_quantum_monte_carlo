# AI usage

## Tools

| Tool | Model and version | Used for |
|---|---|---|
| Claude Code (cloud session, inside a Claude project) | Anthropic Claude Opus 5.5 (`claude-opus-5-5`, 1M context) | design plan, all application code, tests, Terraform, CI configuration, scripts, README and this file |

The candidate (Paawan) directed the work, set the guardrails, made the design
decisions listed below, and reviews the output. Commits made by the assistant
carry a `Co-Authored-By: Claude` trailer.

## Inputs that materially shaped the solution

- The assessment brief (not committed; paraphrased in README).
- A written set of compliance and project guardrails from the candidate. The
  ones that changed the design most: Qrypt is the only entropy source (fail
  closed, no runtime mock switch, CI grep of the seed path); replay must
  recompute and be compared on float64 hex with a sorted `math.fsum` reduction;
  two separate Black-Scholes tests with the flake rate stated; no
  `data "aws_ssm_parameter"` on secrets; provisioned DynamoDB counted per
  account including GSIs; metric dimensions without job ids; reserved
  concurrency as the real cost limiter; honest README wording.
- A design and build plan (a Claude Doc written by the assistant and reviewed
  by the candidate) choosing Python 3.13 on arm64 with pinned numpy,
  `SeedSequence` spawn keys, SHA-256 of the 1 KiB block as the master seed,
  float-hex storage, three Lambdas, a bearer key on a NONE-auth Function URL,
  a 5,000 KiB entropy ceiling and a 14-per-10 s Qrypt limiter.

## Model-generated artifacts and how far each is verified

| Artifact | Verified by | Not verified |
|---|---|---|
| `src/qmc/**` (all deployed code, including the console) | 225 unit, moto-backed and adversarial tests (93% line+branch coverage); ruff; the seed-path grep; `terraform apply` succeeded and a second plan shows no drift | other regions, other Qrypt endpoints, and load beyond the measured runs; see `bench/deployed-checks-2026-10-14.md` for what ran on AWS against the real Qrypt API |
| `eval/adversarial/**` | run green; each case was read for whether it can fail | that the hostile strings cover every attack form |
| `tests/**` | run green locally; each test was read for whether it can fail (the statistical test is intentionally non-deterministic) | that the test suite covers every edge case the brief lists |
| `infra/**` (Terraform) | `terraform validate`, `terraform plan` (39 resources with the S3 code bucket, no secret values in plan output), tflint (preset `all`), Checkov (0 failed; every suppression has an inline reason) | `apply` succeeded (first without reservations, re-applied with them on 2026-10-15) and a second plan showed no drift; a throwaway stack was applied from empty (85 s) and destroyed cleanly (123 s) on 2026-10-15; the dev stack itself has not been destroyed (planned after grading) |
| `.gitlab-ci.yml` | GitLab's CI lint API reports it valid; MR and main pipelines ran green on the project runner; auto-cancel observed; the docs-only skip on main; the OIDC `plan` job and a manual `apply` ran green on 2026-10-15; the nightly schedule ran the `heavy` lane (browser smoke) and a no-drift plan; per-job wall time and peak memory recorded from job logs (MR !2) | per-job memory of every process combined (the logs record the largest single process) |
| `.pre-commit-config.yaml` | hook timings measured; a deliberate lint error blocked a commit | |
| `scripts/build.py` | rebuilt twice with byte-identical zips; packaging test; worker zip compared entry by entry between CI and a local build (only pip's `bin/` scripts differed, now excluded) | that the arm64 numpy wheel imports on Lambda |
| `scripts/bench.py`, `scripts/deployed_failclosed_test.py`, `scripts/qmc_cli.py` | lint; run against the deployed stack on 2026-10-14/15 (1M-path job, replay, benchmarks, fail-closed, DLQ alarm; results in `bench/`) | runs from networks other than one US cloud container |
| `src/qmc/console/*` (web console UI and its copy) | designed after the build on a Claude Design canvas on the candidate's Forge design system and approved by the candidate on 2026-10-15, then restyled to match; Playwright smoke test against a moto-backed local server and, on 2026-10-15, against the deployed stack (fault path local only); screenshots reviewed by the assistant | real browsers other than Chromium; the fault-injection path on the deployment; accessibility audit beyond keyboard focus and contrast choices |
| `scripts/verify.py` | test against a moto-backed job bundle; a real deployed bundle verified `bit-identical` on x86_64 | |
| README cost arithmetic | arithmetic re-checked by hand; Lambda durations measured from REPORT log lines (`scripts/bench.py gbs`, 2026-10-15); Lambda allowances and the AWS Budgets pricing page checked 2026-10-15 | the other services' free-tier figures were not re-checked against current AWS pages |
| README and this file | read by the assistant against the guardrails | candidate review pending |
| Design plan (Claude Doc, outside the repo) | candidate review | |

The system has no AI feature: prices come from simulation and are checked
against a closed form by deterministic tests. The console's interface copy was
drafted with AI assistance, and its footer says so.
