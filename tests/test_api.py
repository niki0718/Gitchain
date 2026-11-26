"""
REST API tests — all endpoints, no network layer needed.

Uses FastAPI's TestClient (httpx-based) to hit every route
against a real in-memory Blockchain and Mempool.
"""

import os
import pytest
from fastapi.testclient import TestClient

from gitchain.api.server import app, set_node
from gitchain.core.blockchain import Blockchain
from gitchain.core.mempool import Mempool
from gitchain.core.miner import mine_block
from gitchain.core.transaction import Transaction
from gitchain.identity.keygen import generate_keypair
from gitchain.identity.signer import sign_payload

DIFFICULTY = 2


# ---------------------------------------------------------------------------
# Fake node — satisfies the same interface node.py exposes to routes.py
# ---------------------------------------------------------------------------

class FakeNode:
    def __init__(self, tmp_path):
        self.blockchain = Blockchain(
            difficulty=DIFFICULTY,
            data_path=str(tmp_path / "chain.json"),
        )
        self.mempool = Mempool()
        self._accepted = True     # control whether submit_transaction succeeds

    async def submit_transaction(self, tx: Transaction) -> bool:
        return self.mempool.add(tx, verify=True)

    def status(self) -> dict:
        return {
            "node_id":      "test-node",
            "chain_height": self.blockchain.height(),
            "mempool_size": self.mempool.size(),
            "peer_count":   2,
            "peers":        ["192.168.1.2:6331", "192.168.1.3:6331"],
        }


def make_tx(private_hex, public_hex, commit="a" * 40) -> Transaction:
    tx = Transaction(
        commit_hash=commit,
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
    return tx


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture
def client(tmp_path, keypair):
    """Set up a TestClient with a fresh FakeNode and one mined block."""
    private_hex, public_hex = keypair
    fake_node = FakeNode(tmp_path)

    # Mine one block with one transaction into the chain
    tx = make_tx(private_hex, public_hex, "a" * 40)
    block = mine_block(
        [tx.to_dict()],
        fake_node.blockchain.tip.hash,
        index=1,
        difficulty=DIFFICULTY,
    )
    fake_node.blockchain.append_block(block)

    set_node(fake_node)

    # Re-import routes now that node is set
    from gitchain.api import routes
    if not any(r.path == "/status" for r in app.routes):
        app.include_router(routes.router)

    return TestClient(app), fake_node, private_hex, public_hex


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

def test_status(client):
    c, node, *_ = client
    resp = c.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "test-node"
    assert data["chain_height"] == 2
    assert data["peer_count"] == 2


# ---------------------------------------------------------------------------
# GET /chain
# ---------------------------------------------------------------------------

def test_get_chain(client):
    c, node, *_ = client
    resp = c.get("/chain")
    assert resp.status_code == 200
    chain = resp.json()
    assert len(chain) == 2
    assert chain[0]["index"] == 0     # genesis
    assert chain[1]["index"] == 1     # mined block
    assert chain[1]["hash"].startswith("00")    # difficulty=2


# ---------------------------------------------------------------------------
# GET /block/:index
# ---------------------------------------------------------------------------

def test_get_block_genesis(client):
    c, *_ = client
    resp = c.get("/block/0")
    assert resp.status_code == 200
    assert resp.json()["index"] == 0


def test_get_block_not_found(client):
    c, *_ = client
    resp = c.get("/block/999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /contributions/:author
# ---------------------------------------------------------------------------

def test_contributions_by_author(client, keypair):
    c, node, private_hex, public_hex = client
    resp = c.get(f"/contributions/{public_hex}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["transactions"][0]["commit_hash"] == "a" * 40


def test_contributions_by_author_unknown(client):
    c, *_ = client
    resp = c.get("/contributions/" + "00" * 32)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# GET /contributions/repo/:org/:repo
# ---------------------------------------------------------------------------

def test_contributions_by_repo(client):
    c, *_ = client
    resp = c.get("/contributions/repo/org/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "org/repo"
    assert data["count"] == 1


# ---------------------------------------------------------------------------
# GET /verify/:commit_hash
# ---------------------------------------------------------------------------

def test_verify_on_chain_commit(client):
    c, *_ = client
    resp = c.get("/verify/" + "a" * 40)
    assert resp.status_code == 200
    data = resp.json()
    assert data["on_chain"] is True
    assert data["block_index"] == 1
    assert data["transaction"]["commit_hash"] == "a" * 40


def test_verify_missing_commit(client):
    c, *_ = client
    resp = c.get("/verify/" + "f" * 40)
    assert resp.status_code == 200
    data = resp.json()
    assert data["on_chain"] is False
    assert data["block_index"] is None


# ---------------------------------------------------------------------------
# GET /mempool
# ---------------------------------------------------------------------------

def test_get_mempool_empty(client):
    c, *_ = client
    resp = c.get("/mempool")
    assert resp.status_code == 200
    assert resp.json()["size"] == 0


# ---------------------------------------------------------------------------
# POST /transaction
# ---------------------------------------------------------------------------

def test_post_valid_transaction(client, keypair):
    c, node, private_hex, public_hex = client
    tx = make_tx(private_hex, public_hex, "b" * 40)
    resp = c.post("/transaction", json=tx.to_dict())
    assert resp.status_code == 201
    assert resp.json()["commit_hash"] == "b" * 40
    assert node.mempool.contains("b" * 40)


def test_post_invalid_signature(client, keypair):
    c, node, private_hex, public_hex = client
    tx = make_tx(private_hex, public_hex, "c" * 40)
    bad = tx.to_dict()
    bad["signature"] = "00" * 64
    resp = c.post("/transaction", json=bad)
    assert resp.status_code == 400


def test_post_tampered_payload_hash(client, keypair):
    c, node, private_hex, public_hex = client
    tx = make_tx(private_hex, public_hex, "d" * 40)
    bad = tx.to_dict()
    bad["payload_hash"] = "ff" * 32   # doesn't match fields
    resp = c.post("/transaction", json=bad)
    assert resp.status_code == 400


def test_post_duplicate_transaction(client, keypair):
    c, node, private_hex, public_hex = client
    tx = make_tx(private_hex, public_hex, "a" * 40)   # already on-chain
    resp = c.post("/transaction", json=tx.to_dict())
    assert resp.status_code == 409
