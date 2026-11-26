"""Tests for Block dataclass and hash calculation."""

import pytest
from gitchain.core.block import Block, create_genesis_block


def test_genesis_block_fields():
    g = create_genesis_block()
    assert g.index == 0
    assert g.previous_hash == "0" * 64
    assert g.transactions == []
    assert len(g.hash) == 64  # SHA-256 hex = 64 chars


def test_genesis_block_hash_is_stable():
    """Genesis hash must be identical across calls (deterministic)."""
    g1 = create_genesis_block()
    g2 = create_genesis_block()
    assert g1.hash == g2.hash


def test_calculate_hash_is_deterministic():
    block = Block(
        index=1,
        timestamp=1_700_000_000.0,
        transactions=[],
        previous_hash="a" * 64,
        nonce=42,
    )
    h1 = block.calculate_hash()
    h2 = block.calculate_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_hash_changes_when_nonce_changes():
    block = Block(
        index=1,
        timestamp=1_700_000_000.0,
        transactions=[],
        previous_hash="a" * 64,
        nonce=0,
    )
    h0 = block.calculate_hash()
    block.nonce = 1
    h1 = block.calculate_hash()
    assert h0 != h1


def test_seal_sets_hash():
    block = Block(
        index=1,
        timestamp=1_700_000_000.0,
        transactions=[],
        previous_hash="a" * 64,
        nonce=5,
    )
    assert block.hash == ""
    block.seal()
    assert block.hash == block.calculate_hash()


def test_to_dict_and_from_dict_roundtrip():
    block = Block(
        index=3,
        timestamp=1_700_000_000.0,
        transactions=[{"commit_hash": "abc123"}],
        previous_hash="b" * 64,
        nonce=99,
    )
    block.seal()
    restored = Block.from_dict(block.to_dict())
    assert restored.index == block.index
    assert restored.hash == block.hash
    assert restored.nonce == block.nonce
    assert restored.transactions == block.transactions


def test_hash_changes_when_transactions_change():
    block1 = Block(1, 1_700_000_000.0, [], "a" * 64, 0)
    block2 = Block(1, 1_700_000_000.0, [{"commit_hash": "xyz"}], "a" * 64, 0)
    assert block1.calculate_hash() != block2.calculate_hash()
