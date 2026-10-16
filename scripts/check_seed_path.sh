#!/usr/bin/env bash
# Fail-closed seeding check: deployed code must have no local entropy or clock
# source that could seed a simulation. Qrypt is the only entropy source.
# Scope: everything under src/ (the code that is packaged into Lambda).
# The mock Qrypt client lives in tests/ and is never packaged.
set -euo pipefail
cd "$(dirname "$0")/.."
# Any import of random/secrets/uuid, any bare random./secrets. call (np.random.* and
# self.secrets.* are preceded by a dot and stay allowed), and the usual clock and
# OS entropy calls. A seeded default_rng(x) is not listed: sim.py derives every
# generator from the Qrypt master seed via SeedSequence, which tests pin.
pattern='os\.urandom|getrandbits|time\.time|uuid[1-5]?\(|default_rng\(\)|SeedSequence\(\)|^[[:space:]]*(import|from)[[:space:]]+(random|secrets|uuid)\b|(^|[^.[:alnum:]_])(random|secrets)\.'
if grep -rnE "$pattern" src/; then
  echo "check_seed_path: forbidden local entropy/clock source found in src/" >&2
  exit 1
fi
echo "check_seed_path: OK (no local entropy sources in src/)"
