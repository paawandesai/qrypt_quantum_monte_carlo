# AI usage

## Tools

| Tool | Model and version | Used for |
|---|---|---|
| Claude Code (cloud session, inside a Claude project) | Anthropic Claude Opus 5.5 (`claude-opus-5-5`, 1M context) | design plan, all application code, tests, Terraform, CI configuration, scripts, README and this file |
| Claude Docs (inside the same project) | the same model | the design and build plan document (outside the repo) |
| Claude Design (inside the same project) | the same model | the web console's layout and visual design, on the candidate's Forge design system, before it was built in vanilla HTML/CSS/JS |

The candidate (Paawan) directed the work, set the guardrails, made the design
decisions listed below, and reviewed the output (README and this file on
2026-10-17). Commits made by the assistant carry a `Co-Authored-By: Claude`
trailer.

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
  by the candidate; it lives outside the repo, and its decisions are the ones
  listed here) choosing Python 3.13 on arm64 with pinned numpy,
  `SeedSequence` spawn keys, SHA-256 of the 1 KiB block as the master seed,
  float-hex storage, three Lambdas, a bearer key on a NONE-auth Function URL,
  a 5,000 KiB entropy ceiling and a 14-per-10 s Qrypt limiter.

## Prompts and configuration, quoted

The guardrails were set as standing instructions for every assistant session in
the project, so they acted as the main prompt configuration. These lines, quoted
verbatim from the candidate's guardrails, shaped the design most:

> Qrypt is the ONLY entropy source. If the entropy request fails
> (401/403/429/5xx/timeout), the job is rejected with the cause in the error
> body. There is no fallback to `os.urandom`, `secrets`, `random`, `time`,
> `uuid`, or any local RNG.

> Replay RECOMPUTES. It never returns the stored result and calls it a replay.

> The replay test asserts equality on the float64 hex of estimate, standard
> error, and CI bounds. Never `pytest.approx`. If exact equality cannot be
> achieved, README says "deterministic to N ulps" and never "bit-identical".

> Terraform never uses `data "aws_ssm_parameter"` on the secret: with
> decryption (the default) the plaintext lands in state and in `terraform plan`
> output.

> `reserved_concurrent_executions` on workers is required; Function URLs have
> no throttling except through reserved concurrency (max RPS = 10x reserved).

> There is no model at runtime; keep it that way (no LLM in the submit, worker,
> or replay path).

Working rules given in the conversation, paraphrased: the assistant runs plans
and tests freely but asks before every `terraform apply` or `destroy` and
applies only from a fresh `main`; it never bypasses commit hooks or weakens a
check without asking; the one-line description is the candidate's own words.
Where a guardrail conflicted with the brief or with AWS, the conflict is
recorded in the README's Known constraints table rather than resolved silently.

## Model-generated artifacts and how far each is verified

| Artifact | Verified by | Not verified |
|---|---|---|
| `src/qmc/**` (all deployed code, including the console) | 242 unit, moto-backed and adversarial tests (94% line+branch coverage); ruff; the seed-path grep; `terraform apply` succeeded and a second plan shows no drift | other regions, other Qrypt endpoints, and load beyond the measured runs; see `bench/deployed-checks-2026-10-14.md`, `-2026-10-16.md`, `-2026-10-16-mr9.md` and `bench/rebuild-2026-10-15.md` for what ran on AWS against the real Qrypt API |
| `eval/adversarial/**` | run green; each case was read for whether it can fail | that the hostile strings cover every attack form |
| `tests/**` | run green locally; each test was read for whether it can fail (the statistical test is intentionally non-deterministic) | that the test suite covers every edge case the brief lists |
| `infra/**` (Terraform) | `terraform validate`, `terraform plan` (39 resources with the S3 code bucket, no secret values in plan output), tflint (preset `all`), Checkov (0 failed; every suppression has an inline reason) | `apply` succeeded (first without reservations, re-applied with them on 2026-10-15) and a second plan showed no drift; a throwaway stack was applied from empty (85 s) and destroyed cleanly (123 s) on 2026-10-15; the dev stack itself has not been destroyed (planned after grading) |
| `.gitlab-ci.yml` | GitLab's CI lint API reports it valid; MR and main pipelines ran green on the project runner; auto-cancel observed; the docs-only skip on main; the OIDC `plan` job and a manual `apply` ran green on 2026-10-15; the nightly schedule ran the `heavy` lane (browser smoke) and a no-drift plan; per-job wall time and peak memory recorded from job logs (MR !2) | per-job memory of every process combined (the logs record the largest single process) |
| `.pre-commit-config.yaml` | hook timings measured; a deliberate lint error blocked a commit | |
| `scripts/build.py` | rebuilt twice with byte-identical zips; packaging test; worker zip compared entry by entry between CI and a local build (only pip's `bin/` scripts differed, now excluded) | that the arm64 numpy wheel imports on Lambda |
| `scripts/bench.py`, `scripts/deployed_failclosed_test.py`, `scripts/qmc_cli.py` | lint; run against the deployed stack on 2026-10-14, 10-15 and 10-16 (1M-path jobs, replays, benchmarks, fail-closed, DLQ alarm rehearsals; results in `bench/`) | runs from networks other than one US cloud container |
| `src/qmc/console/*` (web console UI and its copy) | designed after the build on a Claude Design canvas on the candidate's Forge design system and approved by the candidate on 2026-10-15, then restyled to match; Playwright smoke test against a moto-backed local server and, by hand on 2026-10-15, against the deployed stack (fault path local only; that run's output was not committed); screenshots reviewed by the assistant | real browsers other than Chromium; the fault-injection path on the deployment; accessibility audit beyond keyboard focus and contrast choices |
| `scripts/verify.py` | test against a moto-backed job bundle; a real deployed bundle verified `bit-identical` on x86_64 | |
| README cost arithmetic | arithmetic re-checked by hand; Lambda durations measured from REPORT log lines (`scripts/bench.py gbs`, 2026-10-15); Lambda allowances and the AWS Budgets pricing page checked 2026-10-15 | the other services' free-tier figures were not re-checked against current AWS pages |
| README and this file | read by the assistant against the guardrails; repeated independent review passes by fresh assistant sessions against the brief, each finding checked against the code or the live stack before it was acted on; reviewed by the candidate on 2026-10-17 | |
| Design plan (Claude Doc, outside the repo) | candidate review | |

The system has no AI feature: prices come from simulation and are checked
against a closed form by deterministic tests. The console's interface copy was
drafted with AI assistance, and its footer says so.
