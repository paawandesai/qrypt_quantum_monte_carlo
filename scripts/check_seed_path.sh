#!/usr/bin/env bash
# Fail-closed seeding check: deployed code must have no local entropy or clock
# source that could seed a simulation. Qrypt is the only entropy source.
# Scope: everything under src/ (the code that is packaged into Lambda).
# The mock Qrypt client lives in tests/ and is never packaged.
set -euo pipefail
cd "$(dirname "$0")/.."
pattern='os\.urandom|secrets\.token|random\.seed|time\.time|uuid4|uuid1|random\.random|default_rng\(\)|SeedSequence\(\)'
if grep -rnE "$pattern" src/; then
  echo "check_seed_path: forbidden local entropy/clock source found in src/" >&2
  exit 1
fi
echo "check_seed_path: OK (no local entropy sources in src/)"
