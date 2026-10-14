#!/usr/bin/env bash
# Pre-commit guard: refuse a commit whose staged diff looks like it carries a
# secret or the confidential PRD. Runs alongside gitleaks (see .pre-commit-config.yaml).
set -euo pipefail

status=0
# Added lines only, excluding this script and docs that name the patterns on purpose.
added=$(git diff --cached -U0 -- . ':(exclude)scripts/check_staged.sh' ':(exclude).gitlab-ci.yml' \
  | grep -E '^\+' | grep -vE '^\+\+\+' || true)

if printf '%s\n' "$added" | grep -inE 'api[_-]?key\s*[:=]\s*["'\'']?[A-Za-z0-9_\-]{16,}|secret\s*[:=]\s*["'\'']?[A-Za-z0-9/+_\-]{16,}|Bearer [A-Za-z0-9._\-]{16,}|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|passw(or)?d\s*[:=]\s*["'\'']?[^$"'\''[:space:]{][^[:space:]"'\'']{5,}' ; then
  echo "check_staged: staged diff contains something that looks like a credential" >&2
  status=1
fi

# The PRD is a confidential hiring document and never enters the repo.
if git diff --cached --name-only | grep -iE 'prd|\.docx$|\.pdf$' ; then
  echo "check_staged: a PRD-like file is staged; the PRD must not be committed" >&2
  status=1
fi
exit $status
