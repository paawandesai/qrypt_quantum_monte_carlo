"""Seed derivation.

master seed  = SHA-256("qmc/master-seed/v1" || 1 KiB Qrypt block)  -> 256 bits
shard seed i = numpy SeedSequence(entropy=int(master), spawn_key=(i,))

`SeedSequence(m).spawn(n)[i]` and `SeedSequence(m, spawn_key=(i,))` are the
same sequence, so a worker derives its own shard seed without spawning all of
them. No clock, no global RNG, no local entropy: the only input is the Qrypt
block.
"""

from __future__ import annotations

import hashlib
import re

from .qrypt import BLOCK_BYTES

MASTER_DOMAIN = b"qmc/master-seed/v1"
SEED_DERIVATION = "sha256('qmc/master-seed/v1' || qrypt_block) -> numpy SeedSequence spawn_key=(i,)"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def master_seed_from_block(block: bytes) -> str:
    """Return the 256-bit master seed as 64 lowercase hex chars."""
    if len(block) != BLOCK_BYTES:
        raise ValueError(f"entropy block must be {BLOCK_BYTES} bytes")
    return hashlib.sha256(MASTER_DOMAIN + block).hexdigest()


def block_sha256(block: bytes) -> str:
    """Hash of the raw block, stored so anyone holding the block can link it."""
    return hashlib.sha256(block).hexdigest()


def check_master_seed(master_hex: str) -> int:
    if not isinstance(master_hex, str) or not _HEX64.match(master_hex):
        raise ValueError("master seed must be 64 lowercase hex characters")
    return int(master_hex, 16)


def shard_seed_sequence(master_hex: str, shard_index: int):
    """numpy SeedSequence for one shard (imports numpy lazily; api has none)."""
    from numpy.random import SeedSequence

    if shard_index < 0:
        raise ValueError("shard_index must be >= 0")
    return SeedSequence(entropy=check_master_seed(master_hex), spawn_key=(shard_index,))


def shard_seed_fingerprint(master_hex: str, shard_index: int) -> str:
    """First 128 bits of the shard's seed state, as hex; for tests and display."""
    words = shard_seed_sequence(master_hex, shard_index).generate_state(4, dtype="uint32")
    return "".join(f"{int(w):08x}" for w in words)
