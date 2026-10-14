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
| `src/qmc/**` (all deployed code, including the console) | 137 unit and moto-backed tests (93% line+branch coverage); ruff; the seed-path grep | behaviour on real AWS and against the real Qrypt API (not deployed yet) |
| `tests/**` | run green locally; each test was read for whether it can fail (the statistical test is intentionally non-deterministic) | that the test suite covers every edge case the brief lists |
| `infra/**` (Terraform) | `terraform validate`, `terraform plan` (34 resources, no secret values in plan output), tflint (preset `all`), Checkov (0 failed; every suppression has an inline reason) | `apply`, `destroy`, and the "no drift on second plan" check have not run |
| `.gitlab-ci.yml` | GitLab's CI lint API reports it valid; MR and main pipelines ran green on the project runner; auto-cancel observed | the `heavy` lane (no tagged runner), the docs-only skip on main, the OIDC plan/apply jobs (role not created), per-job peak memory |
| `.pre-commit-config.yaml` | hook timings measured; a deliberate lint error blocked a commit | |
| `scripts/build.py` | rebuilt twice with byte-identical zips; packaging test | that the arm64 numpy wheel imports on Lambda |
| `scripts/bench.py`, `scripts/deployed_failclosed_test.py`, `scripts/qmc_cli.py` | lint only | not run against a deployment yet |
| `src/qmc/console/*` (web console UI and its copy) | Playwright smoke test against a moto-backed local server; screenshots reviewed by the assistant | real browsers other than Chromium; a deployment; accessibility audit beyond keyboard focus and contrast choices |
| `scripts/verify.py` | test against a moto-backed job bundle | a real deployed bundle |
| README cost arithmetic | arithmetic re-checked by hand | Lambda durations are estimates; AWS free-tier figures not re-checked against current AWS pages from this environment |
| README and this file | read by the assistant against the guardrails | candidate review pending |
| Design plan (Claude Doc, outside the repo) | candidate review | |

The system has no AI feature: prices come from simulation and are checked
against a closed form by deterministic tests. The console's interface copy was
drafted with AI assistance, and its footer says so.
