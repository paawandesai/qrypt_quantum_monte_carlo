# Licenses

Audit run with `pip-licenses --from=mixed` (re-run before submission; CI's
`license-audit` job regenerates it and fails on GPL/AGPL, with partial matching so "GPLv2", "GPLv3+" and SPDX ids are caught too). That job reads pip metadata only, so libraries vendored inside a wheel (numpy's, below) were checked by hand from the built zip. Last run: 2026-10-15; vendored libraries checked 2026-10-17.
`pip-licenses` leaves itself and its own dependencies out of its report by default; they are dev-only and checked by hand: pip-licenses 5.5.5 (MIT), prettytable 3.18.0 (BSD-3-Clause), wcwidth 0.9.2 (MIT).

## Packaged into Lambda

| Package | Version | License | Where |
|---|---|---|---|
| numpy | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0). The arm64 wheel also vendors two native libraries in `numpy.libs/`: OpenBLAS (BSD-3-Clause) and libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1; the GCC Runtime Library Exception permits use by non-GPL programs). Their license texts ship inside numpy's `dist-info/licenses/LICENSE.txt` in the zip | worker.zip |
| boto3 / botocore | runtime-provided | Apache-2.0 | provided by the AWS Lambda Python 3.13 runtime, not bundled |

The api and reconciler zips contain only this repository's code.

## Tooling (not distributed with the app)

| Tool | Version | License | Note |
|---|---|---|---|
| Terraform | 1.16.6 | BUSL-1.1 | Acceptable for this use (we do not offer Terraform as a competing hosted product). OpenTofu is a drop-in if an OSI-only policy applies. |
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
| nodeenv            | 1.11.0      | BSD License                                        |
| Pygments           | 2.21.0      | BSD-2-Clause                                       |
| MarkupSafe         | 3.0.4       | BSD-3-Clause                                       |
| Werkzeug           | 3.1.9       | BSD-3-Clause                                       |
| idna               | 3.20        | BSD-3-Clause                                       |
| pycparser          | 3.11        | BSD-3-Clause                                       |
| numpy              | 2.5.4       | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| ast_serialize      | 0.13.0      | MIT                                                |
| cfgv               | 3.5.0       | MIT                                                |
| charset-normalizer | 3.5.2       | MIT                                                |
| execnet            | 2.1.2       | MIT                                                |
| filelock           | 4.1.2       | MIT                                                |
| identify           | 2.6.20      | MIT                                                |
| iniconfig          | 2.3.1       | MIT                                                |
| librt              | 0.16.0      | MIT                                                |
| mypy               | 2.4.0       | MIT                                                |
| mypy_extensions    | 1.1.0       | MIT                                                |
| platformdirs       | 4.13.0      | MIT                                                |
| pre_commit         | 4.7.0       | MIT                                                |
| pytest             | 9.1.1       | MIT                                                |
| pytest-cov         | 7.1.0       | MIT                                                |
| pytest-xdist       | 3.8.0       | MIT                                                |
| ruff               | 0.17.0      | MIT                                                |
| urllib3            | 2.8.0       | MIT                                                |
| virtualenv         | 21.14.7     | MIT                                                |
| xmltodict          | 1.0.4       | MIT                                                |
| PyYAML             | 6.0.3       | MIT License                                        |
| jmespath           | 1.1.0       | MIT License                                        |
| pluggy             | 1.6.0       | MIT License                                        |
| python-discovery   | 1.6.2       | MIT License                                        |
| six                | 1.17.0      | MIT License                                        |
| cffi               | 2.1.1       | MIT-0                                              |
| certifi            | 2026.7.22   | Mozilla Public License 2.0 (MPL 2.0)               |
| pathspec           | 1.1.1       | Mozilla Public License 2.0 (MPL 2.0)               |
| typing_extensions  | 4.16.0      | PSF-2.0                                            |
| distlib            | 0.4.3       | Python Software Foundation License                 |