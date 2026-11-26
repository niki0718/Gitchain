"""Tests for Transaction dataclass, payload hashing, and signature verification."""

import time
import pytest

from gitchain.identity.keygen import generate_keypair
from gitchain.identity.signer import sign_payload, pubkey_from_private
from gitchain.core.transaction import Transaction


def make_transaction(private_hex: str, public_hex: str, **overrides) -> Transaction:
    """Helper: build a fully signed Transaction."""
    fields = {
        "commit_hash": "a" * 40,
        "owner_pubkey": public_hex,
        "author": "test-user",
        "repo": "org/repo",
        "branch": "main",
        "timestamp": 1_700_000_000.0,
        "diff_summary": "3 files changed, +120 -40",
        "diff_hash": "abc123" * 10 + "abcd",
    }
    fields.update(overrides)

    # Compute payload hash from the base fields
    tx = Transaction(
        **fields,
        payload_hash="",
        signature="",
    )
    tx.payload_hash = tx.compute_payload_hash()
    tx.signature = sign_payload(tx.payload_hash, private_hex)
    return tx


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


def test_payload_hash_is_deterministic(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    h1 = tx.compute_payload_hash()
    h2 = tx.compute_payload_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_payload_hash_changes_with_field_change(keypair):
    private_hex, public_hex = keypair
    tx1 = make_transaction(private_hex, public_hex, commit_hash="a" * 40)
    tx2 = make_transaction(private_hex, public_hex, commit_hash="b" * 40)
    assert tx1.compute_payload_hash() != tx2.compute_payload_hash()


def test_is_payload_hash_valid(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    assert tx.is_payload_hash_valid()


def test_is_payload_hash_invalid_after_tamper(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    tx.repo = "evil/repo"  # tamper without recomputing hash
    assert not tx.is_payload_hash_valid()


def test_valid_signature_passes(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    assert tx.verify_signature()


def test_invalid_signature_rejected(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    tx.signature = "00" * 64   # garbage signature
    assert not tx.verify_signature()


def test_wrong_pubkey_rejected():
    private_hex1, public_hex1 = generate_keypair()
    _, public_hex2 = generate_keypair()
    tx = make_transaction(private_hex1, public_hex1)
    tx.owner_pubkey = public_hex2  # wrong pubkey
    assert not tx.verify_signature()


def test_to_dict_from_dict_roundtrip(keypair):
    private_hex, public_hex = keypair
    tx = make_transaction(private_hex, public_hex)
    restored = Transaction.from_dict(tx.to_dict())
    assert restored.commit_hash == tx.commit_hash
    assert restored.signature == tx.signature
    assert restored.verify_signature()
