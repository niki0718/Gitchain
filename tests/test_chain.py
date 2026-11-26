"""
Tests for Blockchain class, validate_block, validate_chain, resolve_conflict.
"""

import tempfile
import os
import pytest

from gitchain.core.block import Block, create_genesis_block
from gitchain.core.blockchain import (
    Blockchain,
    validate_block,
    validate_chain,
    resolve_conflict,
)
from gitchain.core.miner import mine_block
from gitchain.identity.keygen import generate_keypair
from gitchain.identity.signer import sign_payload
from gitchain.core.transaction import Transaction

DIFFICULTY = 2  # use 2 zeros in tests so they run fast


def make_signed_tx(private_hex: str, public_hex: str, commit_hash: str = "a" * 40) -> dict:
    tx = Transaction(
        commit_hash=commit_hash,
        owner_pubkey=public_hex,
        author="test-user",
        repo="org/repo",
        branch="main",
        timestamp=1_700_000_000.0,
        diff_summary="1 file changed",
        diff_hash="",
        payload_hash="",
        signature="",
    )
    tx.payload_hash = tx.compute_payload_hash()
    tx.signature = sign_payload(tx.payload_hash, private_hex)
    return tx.to_dict()


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture
def tmp_chain(tmp_path):
    """Blockchain backed by a temp file, difficulty=2."""
    chain_file = str(tmp_path / "chain.json")
    return Blockchain(difficulty=DIFFICULTY, data_path=chain_file)


# --------------------------------------------------------------------------
# validate_block
# --------------------------------------------------------------------------

def test_validate_block_valid(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    ok, reason = validate_block(block, genesis, DIFFICULTY)
    assert ok, reason


def test_validate_block_wrong_index(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    block.index = 5   # wrong
    block.seal()
    ok, _ = validate_block(block, genesis, DIFFICULTY)
    assert not ok


def test_validate_block_wrong_previous_hash(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    block.previous_hash = "f" * 64  # wrong
    block.seal()
    ok, _ = validate_block(block, genesis, DIFFICULTY)
    assert not ok


def test_validate_block_tampered_hash(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    block.hash = "0" * 64   # fake a passing hash without recomputing
    ok, _ = validate_block(block, genesis, DIFFICULTY)
    assert not ok


# --------------------------------------------------------------------------
# validate_chain
# --------------------------------------------------------------------------

def test_validate_chain_genesis_only():
    genesis = create_genesis_block()
    ok, reason = validate_chain([genesis], difficulty=DIFFICULTY)
    assert ok, reason


def test_validate_chain_two_blocks(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block1 = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    ok, reason = validate_chain([genesis, block1], difficulty=DIFFICULTY)
    assert ok, reason


def test_validate_chain_detects_tampered_block(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block1 = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    block1.transactions = []   # tamper — hash no longer matches
    ok, _ = validate_chain([genesis, block1], difficulty=DIFFICULTY)
    assert not ok


def test_validate_chain_empty():
    ok, _ = validate_chain([], difficulty=DIFFICULTY)
    assert not ok


# --------------------------------------------------------------------------
# resolve_conflict
# --------------------------------------------------------------------------

def test_resolve_conflict_longer_wins(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx1 = make_signed_tx(private_hex, public_hex, commit_hash="a" * 40)
    tx2 = make_signed_tx(private_hex, public_hex, commit_hash="b" * 40)
    block1 = mine_block([tx1], genesis.hash, index=1, difficulty=DIFFICULTY)
    block2 = mine_block([tx2], block1.hash, index=2, difficulty=DIFFICULTY)

    short_chain = [genesis, block1]
    long_chain = [genesis, block1, block2]

    winner = resolve_conflict(short_chain, long_chain, DIFFICULTY)
    assert len(winner) == 3


def test_resolve_conflict_keeps_local_when_equal(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)
    chain = [genesis, block]

    winner = resolve_conflict(chain, chain, DIFFICULTY)
    assert winner == chain


def test_resolve_conflict_rejects_invalid_longer(keypair):
    private_hex, public_hex = keypair
    genesis = create_genesis_block()
    tx = make_signed_tx(private_hex, public_hex)
    block1 = mine_block([tx], genesis.hash, index=1, difficulty=DIFFICULTY)

    # Build a longer chain that's invalid (tampered)
    block2 = mine_block([tx], block1.hash, index=2, difficulty=DIFFICULTY)
    block2.transactions = [{"commit_hash": "tampered"}]
    # Don't re-seal — hash won't match

    local = [genesis, block1]
    fake_long = [genesis, block1, block2]

    winner = resolve_conflict(local, fake_long, DIFFICULTY)
    assert len(winner) == 2   # local chain kept


# --------------------------------------------------------------------------
# Blockchain class
# --------------------------------------------------------------------------

def test_blockchain_starts_with_genesis(tmp_chain):
    assert tmp_chain.height() == 1
    assert tmp_chain.tip.index == 0


def test_blockchain_append_valid_block(tmp_chain, keypair):
    private_hex, public_hex = keypair
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], tmp_chain.tip.hash, index=1, difficulty=DIFFICULTY)
    ok, reason = tmp_chain.append_block(block)
    assert ok, reason
    assert tmp_chain.height() == 2


def test_blockchain_rejects_invalid_block(tmp_chain, keypair):
    private_hex, public_hex = keypair
    tx = make_signed_tx(private_hex, public_hex)
    block = mine_block([tx], "wrong_hash", index=1, difficulty=DIFFICULTY)
    ok, _ = tmp_chain.append_block(block)
    assert not ok


def test_blockchain_has_transaction(tmp_chain, keypair):
    private_hex, public_hex = keypair
    commit = "c" * 40
    tx = make_signed_tx(private_hex, public_hex, commit_hash=commit)
    block = mine_block([tx], tmp_chain.tip.hash, index=tmp_chain.height(), difficulty=DIFFICULTY)
    tmp_chain.append_block(block)
    assert tmp_chain.has_transaction(commit) is not None
    assert tmp_chain.has_transaction("d" * 40) is None


def test_blockchain_persists_and_loads(tmp_path, keypair):
    """Chain survives a restart (serialize → deserialize)."""
    private_hex, public_hex = keypair
    chain_file = str(tmp_path / "persist_chain.json")
    bc = Blockchain(difficulty=DIFFICULTY, data_path=chain_file)
    tx = make_signed_tx(private_hex, public_hex, commit_hash="e" * 40)
    block = mine_block([tx], bc.tip.hash, index=1, difficulty=DIFFICULTY)
    bc.append_block(block)

    # Reload from disk
    bc2 = Blockchain(difficulty=DIFFICULTY, data_path=chain_file)
    assert bc2.height() == 2
    assert bc2.has_transaction("e" * 40) is not None
