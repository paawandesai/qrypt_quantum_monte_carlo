#!/usr/bin/env bash
# terraform init against GitLab-managed Terraform state.
#
# Needs: GITLAB_HOST, GITLAB_PROJECT_ID, and a token with `api` scope in
# GITLAB_TOKEN (locally) or CI_JOB_TOKEN (in GitLab CI). The state name defaults
# to the environment (TF_STATE_NAME, e.g. "dev" or a branch slug).
set -euo pipefail
cd "$(dirname "$0")/../infra"

: "${GITLAB_HOST:?set GITLAB_HOST}"
: "${GITLAB_PROJECT_ID:?set GITLAB_PROJECT_ID}"
STATE="${TF_STATE_NAME:-dev}"
ADDR="https://${GITLAB_HOST}/api/v4/projects/${GITLAB_PROJECT_ID}/terraform/state/${STATE}"

if [[ -n "${CI_JOB_TOKEN:-}" ]]; then
  export TF_HTTP_USERNAME="gitlab-ci-token" TF_HTTP_PASSWORD="${CI_JOB_TOKEN}"
else
  : "${GITLAB_TOKEN:?set GITLAB_TOKEN (a PAT with api scope)}"
  export TF_HTTP_USERNAME="${GITLAB_USER:-oauth2}" TF_HTTP_PASSWORD="${GITLAB_TOKEN}"
fi

terraform init -input=false -reconfigure \
  -backend-config="address=${ADDR}" \
  -backend-config="lock_address=${ADDR}/lock" \
  -backend-config="unlock_address=${ADDR}/lock" \
  -backend-config="lock_method=POST" \
  -backend-config="unlock_method=DELETE" \
  -backend-config="retry_wait_min=5" "$@"
