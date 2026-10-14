"""PRD: shard seeds are distinct, and the same master seed always yields the same shard seeds."""

import hashlib

import numpy as np
import pytest

from qmc.seed import (
    block_sha256,
    check_master_seed,
    master_seed_from_block,
    shard_seed_fingerprint,
    shard_seed_sequence,
)

BLOCK = bytes(range(256)) * 4
MASTER = master_seed_from_block(BLOCK)


def test_master_seed_is_256_bit_hex_and_domain_separated():
    assert len(MASTER) == 64 and int(MASTER, 16) >= 0
    assert MASTER == hashlib.sha256(b"qmc/master-seed/v1" + BLOCK).hexdigest()
    assert MASTER != block_sha256(BLOCK)
    assert block_sha256(BLOCK) == hashlib.sha256(BLOCK).hexdigest()


def test_master_seed_depends_on_every_byte():
    flipped = bytearray(BLOCK)
    flipped[-1] ^= 1
    assert master_seed_from_block(bytes(flipped)) != MASTER


def test_block_must_be_1_kib():
    with pytest.raises(ValueError):
        master_seed_from_block(b"\x00" * 32)


def test_shard_seeds_are_distinct():
    fps = [shard_seed_fingerprint(MASTER, i) for i in range(1000)]
    assert len(set(fps)) == 1000


def test_same_master_always_gives_same_shard_seeds():
    a = [shard_seed_fingerprint(MASTER, i) for i in range(50)]
    b = [shard_seed_fingerprint(MASTER, i) for i in range(50)]
    assert a == b


def test_direct_derivation_equals_spawn():
    spawned = np.random.SeedSequence(int(MASTER, 16)).spawn(20)
    for i, child in enumerate(spawned):
        direct = shard_seed_sequence(MASTER, i)
        assert np.array_equal(child.generate_state(8), direct.generate_state(8))


def test_different_masters_give_different_shard_seeds():
    other = master_seed_from_block(b"\x01" * 1024)
    assert shard_seed_fingerprint(MASTER, 0) != shard_seed_fingerprint(other, 0)


def test_shard_streams_do_not_overlap_in_first_draws():
    draws = [
        np.random.Generator(np.random.PCG64(shard_seed_sequence(MASTER, i))).integers(0, 2**63, 4) for i in range(10)
    ]
    assert len({tuple(d) for d in draws}) == 10


@pytest.mark.parametrize("bad", ["", "ABCD" * 16, "z" * 64, "ab" * 31, 123])
def test_master_seed_format_checked(bad):
    with pytest.raises(ValueError):
        check_master_seed(bad)


def test_negative_shard_index_rejected():
    with pytest.raises(ValueError):
        shard_seed_sequence(MASTER, -1)
