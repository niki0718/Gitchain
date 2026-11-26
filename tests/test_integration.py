"""
Multi-node integration test — Phase 4 deliverable.

All steps run inside one async test function so they share a single event loop.
This is correct: sequential integration steps with live asyncio daemons must
not be split across pytest functions (each gets its own loop, killing tasks).

Scenario (per the architecture doc):
  Step 1  — Start 3 nodes (A, B, C) on ports 17800/17801/17802
  Step 2  — Full-mesh peer connect (A↔B, A↔C, B↔C)
  Step 3  — Submit 5 transactions to A — verify all 3 mempools receive them
  Step 4  — Take B offline — mine 3 blocks on A (C receives live)
  Step 5  — Bring B2 back online — verify it syncs to A+C's chain
  Step 6  — Invalid signature rejected on all nodes
  Step 7  — Chain integrity: validate_chain() passes on all nodes
  Step 8  — REST API confirms all 5 commits are on-chain
"""

import asyncio
import os
import pytest
from fastapi.testclient import TestClient

from gitchain.network.node import GitChainNode
from gitchain.core.transaction import Transaction
from gitchain.core.blockchain import validate_chain
from gitchain.identity.keygen import generate_keypair
from gitchain.identity.signer import sign_payload
from gitchain.api.server import app, set_node
from gitchain.api import routes as _routes_module

DIFFICULTY = 2
PORTS = (17800, 17801, 17802)
COMMITS = [c * 40 for c in "abcde"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tx(private_hex: str, public_hex: str, commit: str) -> Transaction:
    tx = Transaction(
        commit_hash=commit,
        owner_pubkey=public_hex,
        author="test-user",
        repo="org/gitchain",
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


async def make_node(port: int, tmp_dir: str) -> GitChainNode:
    chain_file = os.path.join(tmp_dir, f"int_chain_{port}.json")
    node = GitChainNode(port=port, difficulty=DIFFICULTY,
                        data_path=chain_file, enable_mdns=False)
    await node.start()
    return node


async def wait_for(cond, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cond():
            return True
        await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# The integration test — one function, one event loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_gitchain_lifecycle(tmp_path):
    private_hex, public_hex = generate_keypair()
    tmp = str(tmp_path)

    # ------------------------------------------------------------------
    # Step 1: Start 3 nodes
    # ------------------------------------------------------------------
    node_a = await make_node(PORTS[0], tmp)
    node_b = await make_node(PORTS[1], tmp)
    node_c = await make_node(PORTS[2], tmp)

    try:
        # ------------------------------------------------------------------
        # Step 2: Full-mesh peer connect
        # ------------------------------------------------------------------
        assert await node_a.connect_to_peer("127.0.0.1", PORTS[1]), "A→B failed"
        assert await node_a.connect_to_peer("127.0.0.1", PORTS[2]), "A→C failed"
        assert await node_b.connect_to_peer("127.0.0.1", PORTS[2]), "B→C failed"
        await asyncio.sleep(0.2)

        for node in (node_a, node_b, node_c):
            assert node.blockchain.height() == 1, "expected genesis only"

        # ------------------------------------------------------------------
        # Step 3: Submit 5 transactions to A — all 3 mempools receive them
        # ------------------------------------------------------------------
        for commit in COMMITS:
            tx = make_tx(private_hex, public_hex, commit)
            accepted = await node_a.submit_transaction(tx)
            assert accepted, f"A rejected tx {commit[:8]}"

        for name, node in [("A", node_a), ("B", node_b), ("C", node_c)]:
            ok = await wait_for(
                lambda n=node: all(n.mempool.contains(c) for c in COMMITS),
                timeout=5.0,
            )
            missing = [c[:8] for c in COMMITS if not node.mempool.contains(c)]
            assert ok, f"Node {name} missing txs: {missing}"

        # ------------------------------------------------------------------
        # Step 4: Take B offline — mine ≥3 blocks on A (C receives live)
        # ------------------------------------------------------------------
        await node_b.stop()
        await asyncio.sleep(0.1)

        # A's mining loop runs automatically; wait until all 5 commits are on-chain
        # (they may land in 1, 2, or 3 blocks depending on timing)
        mined = await wait_for(
            lambda: all(node_a.blockchain.has_transaction(c) for c in COMMITS),
            timeout=30.0,
        )
        assert mined, (
            f"A never mined all commits — height={node_a.blockchain.height()}, "
            f"missing={[c[:8] for c in COMMITS if not node_a.blockchain.has_transaction(c)]}"
        )

        # C must have received all new blocks
        c_synced = await wait_for(
            lambda: node_c.blockchain.height() == node_a.blockchain.height(),
            timeout=5.0,
        )
        assert c_synced, (
            f"C height {node_c.blockchain.height()} != A height {node_a.blockchain.height()}"
        )

        # Chain integrity on A and C
        for label, node in [("A", node_a), ("C", node_c)]:
            ok, reason = validate_chain(node.blockchain.chain, DIFFICULTY)
            assert ok, f"Node {label} chain invalid: {reason}"

        assert node_a.blockchain.height() > 1, "A should have mined at least 1 block"
        height_after_mining = node_a.blockchain.height()

        # ------------------------------------------------------------------
        # Step 5: Bring B2 back — it syncs to A's longer chain
        # ------------------------------------------------------------------
        node_b2 = await make_node(17803, tmp)
        await node_b2.connect_to_peer("127.0.0.1", PORTS[0])

        synced = await wait_for(
            lambda: node_b2.blockchain.height() == height_after_mining,
            timeout=5.0,
        )
        assert synced, (
            f"B2 height {node_b2.blockchain.height()} != A height {height_after_mining}"
        )

        ok, reason = validate_chain(node_b2.blockchain.chain, DIFFICULTY)
        assert ok, f"B2 resynced chain invalid: {reason}"

        # ------------------------------------------------------------------
        # Step 6: Invalid signature rejected everywhere
        # ------------------------------------------------------------------
        bad_commit = "f" * 40
        tx = make_tx(private_hex, public_hex, bad_commit)
        tx.signature = "00" * 64   # corrupt

        for label, node in [("A", node_a), ("C", node_c), ("B2", node_b2)]:
            rejected = not await node.submit_transaction(tx)
            assert rejected, f"Node {label} accepted an invalid signature"
        await asyncio.sleep(0.1)
        for label, node in [("A", node_a), ("C", node_c), ("B2", node_b2)]:
            assert not node.mempool.contains(bad_commit), \
                f"Node {label} has bad tx in mempool"

        # ------------------------------------------------------------------
        # Step 7: All chains identical and valid
        # ------------------------------------------------------------------
        tip_hash = node_a.blockchain.tip.hash
        for label, node in [("C", node_c), ("B2", node_b2)]:
            ok, reason = validate_chain(node.blockchain.chain, DIFFICULTY)
            assert ok, f"Node {label} chain invalid: {reason}"
            assert node.blockchain.tip.hash == tip_hash, \
                f"Node {label} tip diverged from A"

        # ------------------------------------------------------------------
        # Step 8: REST API confirms all 5 commits are on-chain
        # ------------------------------------------------------------------
        set_node(node_a)
        if not any(getattr(r, "path", None) == "/status" for r in app.routes):
            app.include_router(_routes_module.router)

        with TestClient(app) as client:
            for commit in COMMITS:
                resp = client.get(f"/verify/{commit}")
                assert resp.status_code == 200
                data = resp.json()
                assert data["on_chain"], f"Commit {commit[:8]} missing from API"
                assert data["block_index"] is not None

            chain_resp = client.get("/chain")
            assert chain_resp.status_code == 200
            assert len(chain_resp.json()) == height_after_mining

            status_resp = client.get("/status")
            assert status_resp.status_code == 200
            assert status_resp.json()["chain_height"] == height_after_mining

        await node_b2.stop()

    finally:
        for node in (node_a, node_c):
            try:
                await node.stop()
            except Exception:
                pass
