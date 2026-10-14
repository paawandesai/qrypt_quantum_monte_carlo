# Licenses

Audit run with `pip-licenses --from=mixed` (re-run before submission; CI's
`license-audit` job regenerates it and fails on GPL/AGPL). Last run: 2026-10-14.

## Packaged into Lambda

| Package | Version | License | Where |
|---|---|---|---|
| numpy | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) | worker.zip |
| boto3 / botocore | runtime-provided | Apache-2.0 | provided by the AWS Lambda Python 3.13 runtime, not bundled |

The api and reconciler zips contain only this repository's code.

## Tooling (not distributed with the app)

| Tool | Version | License | Note |
|---|---|---|---|
| Terraform | 1.16.6 | BSL 1.1 | Acceptable for this use (we do not offer Terraform as a competing hosted product). OpenTofu is a drop-in if an OSI-only policy applies. |
| hashicorp/aws provider | 6.69.0 | MPL-2.0 | |
| tflint | 0.59.1 | MPL-2.0 | |
| Checkov | 3.3.29 | Apache-2.0 | IaC scanner (tfsec is deprecated into Trivy, so not used) |
| gitleaks | 8.28.0 | MIT | pre-commit hook and CI |

## Python development and test dependencies (requirements-dev.lock)

| Name               | Version     | License                                            |
|--------------------|-------------|----------------------------------------------------|
| requests           | 2.34.2      | Apache Software License                            |
| s3transfer         | 0.19.2      | Apache Software License                            |
| python-dateutil    | 2.9.0.post0 | Apache Software License; BSD License               |
| boto3              | 1.43.114    | Apache-2.0                                         |
| botocore           | 1.43.114    | Apache-2.0                                         |
| coverage           | 7.16.2      | Apache-2.0                                         |
| moto               | 5.2.4       | Apache-2.0                                         |
| responses          | 0.26.3      | Apache-2.0                                         |
| packaging          | 26.3        | Apache-2.0 OR BSD-2-Clause                         |
| cryptography       | 50.0.2      | Apache-2.0 OR BSD-3-Clause                         |
| Pygments           | 2.21.0      | BSD-2-Clause                                       |
| MarkupSafe         | 3.0.4       | BSD-3-Clause                                       |
| Werkzeug           | 3.1.9       | BSD-3-Clause                                       |
| idna               | 3.20        | BSD-3-Clause                                       |
| pycparser          | 3.11        | BSD-3-Clause                                       |
| numpy              | 2.5.4       | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| charset-normalizer | 3.5.2       | MIT                                                |
| iniconfig          | 2.3.1       | MIT                                                |
| pytest             | 9.1.1       | MIT                                                |
| pytest-cov         | 7.1.0       | MIT                                                |
| ruff               | 0.17.0      | MIT                                                |
| urllib3            | 2.8.0       | MIT                                                |
| xmltodict          | 1.0.4       | MIT                                                |
| jmespath           | 1.1.0       | MIT License                                        |
| pluggy             | 1.6.0       | MIT License                                        |
| six                | 1.17.0      | MIT License                                        |
| cffi               | 2.1.1       | MIT-0                                              |
| certifi            | 2026.7.22   | Mozilla Public License 2.0 (MPL 2.0)               |
