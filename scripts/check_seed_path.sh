#!/usr/bin/env bash
# Fail-closed seeding check: deployed code must have no local entropy or clock
# source that could seed a simulation. Qrypt is the only entropy source.
# Scope: everything under src/ (the code that is packaged into Lambda).
# The mock Qrypt client lives in tests/ and is never packaged.
set -euo pipefail
cd "$(dirname "$0")/.."
# Any import naming random/secrets/uuid (also inside "import os, random" or
# "import numpy.random as r"), any bare random./secrets. call (np.random.* and
# self.secrets.* are preceded by a dot and stay allowed), the usual clock and OS
# entropy calls, unseeded generators (default_rng(), default_rng(None),
# SeedSequence(), PCG64()), and dynamic imports that could hide any of these.
# A seeded default_rng(x) is not listed: sim.py derives every generator from the
# Qrypt master seed via SeedSequence, which tests pin.
pattern='urandom|getrandom|getrandbits|time\.time|uuid[1-5]?\(|default_rng\([[:space:]]*(None)?[[:space:]]*\)|SeedSequence\([[:space:]]*(None)?[[:space:]]*\)|PCG64\([[:space:]]*\)|__import__|import_module|^[[:space:]]*(import|from)[[:space:]][^#]*\b(random|secrets|uuid)\b|(^|[^.[:alnum:]_])(random|secrets)\.'
# Importing the seedable numpy classes by name is how sim.py and seed.py get them.
allowed='^[^:]+:[0-9]+:[[:space:]]*from numpy\.random import ((SeedSequence|Generator|PCG64|default_rng)(,[[:space:]]*)?)+$'
hits=$(grep -rnIE --include='*.py' "$pattern" src/ | grep -vE "$allowed" || true)
if [[ -n "$hits" ]]; then
  echo "$hits"
  echo "check_seed_path: forbidden local entropy/clock source found in src/" >&2
  exit 1
fi
echo "check_seed_path: OK (no local entropy sources in src/)"
