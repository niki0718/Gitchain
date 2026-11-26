"""
P2P networking integration tests.

All tests run on localhost with mDNS disabled (manual TCP connect).
Ports are unique per test to avoid conflicts between test runs.

Five scenarios:
  1. Two nodes connect and sync chains
  2. Transaction submitted to A propagates to B's mempool
  3. Block mined on A automatically appears on B
  4. Invalid signature rejected — not propagated
  5. Node that was offline syncs to longer chain on reconnect
"""

import asyncio
import os
import pytest

from gitchain.network.node import GitChainNode
from gitchain.core.transaction import Transaction
from gitchain.core.miner import mine_block
from gitchain.identity.keygen import generate_keypair
from gitchain.identity.signer import sign_payload

DIFFICULTY = 2   # fast: hash needs only "00" prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tx(private_hex: str, public_hex: str, commit: str = "a" * 40) -> Transaction:
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


async def make_node(port: int, tmp_dir: str, difficulty: int = DIFFICULTY) -> GitChainNode:
    chain_file = os.path.join(tmp_dir, f"chain_{port}.json")
    node = GitChainNode(
        port=port,
        difficulty=difficulty,
        data_path=chain_file,
        enable_mdns=False,
    )
    await node.start()
    return node


async def wait_for(condition, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll condition() until True or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


# ---------------------------------------------------------------------------
# Test 1: chain sync on connect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chain_sync_on_connect(keypair, tmp_path):
    """Node B syncs to A's chain when it connects."""
    private_hex, public_hex = keypair
    node_a = await make_node(17700, str(tmp_path))
    node_b = await make_node(17701, str(tmp_path))

    try:
        # Mine a block on A first
        tx = make_tx(private_hex, public_hex, "a" * 40)
        block = mine_block([tx.to_dict()], node_a.blockchain.tip.hash,
                           index=1, difficulty=DIFFICULTY)
        node_a.blockchain.append_block(block)
        assert node_a.blockchain.height() == 2

        # B is still at genesis
        assert node_b.blockchain.height() == 1

        # Connect B → A; A will send CHAIN_RESPONSE immediately
        connected = await node_b.connect_to_peer("127.0.0.1", 17700)
        assert connected

        # Wait for B to process the CHAIN_RESPONSE and adopt A's chain
        synced = await wait_for(lambda: node_b.blockchain.height() == 2)
        assert synced, f"B height={node_b.blockchain.height()}, expected 2"
    finally:
        await node_a.stop()
        await node_b.stop()


# ---------------------------------------------------------------------------
# Test 2: transaction propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transaction_propagates(keypair, tmp_path):
    """Transaction submitted to A appears in B's mempool."""
    private_hex, public_hex = keypair
    node_a = await make_node(17702, str(tmp_path))
    node_b = await make_node(17703, str(tmp_path))

    try:
        await node_b.connect_to_peer("127.0.0.1", 17702)
        await asyncio.sleep(0.1)  # let connection settle

        tx = make_tx(private_hex, public_hex, "b" * 40)
        accepted = await node_a.submit_transaction(tx)
        assert accepted

        propagated = await wait_for(lambda: node_b.mempool.contains("b" * 40))
        assert propagated, "transaction did not propagate to B"
    finally:
        await node_a.stop()
        await node_b.stop()


# ---------------------------------------------------------------------------
# Test 3: mined block propagates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mined_block_propagates(keypair, tmp_path):
    """Block mined on A is automatically received and validated by B."""
    private_hex, public_hex = keypair
    node_a = await make_node(17704, str(tmp_path))
    node_b = await make_node(17705, str(tmp_path))

    try:
        await node_b.connect_to_peer("127.0.0.1", 17704)
        await asyncio.sleep(0.1)

        # Submit a transaction — the mining loop on A will pick it up
        tx = make_tx(private_hex, public_hex, "c" * 40)
        await node_a.submit_transaction(tx)

        # Wait for A to mine (difficulty=2 → nearly instant)
        mined = await wait_for(lambda: node_a.blockchain.height() > 1, timeout=10.0)
        assert mined, "A never mined a block"

        # Wait for B to receive the NEW_BLOCK broadcast
        synced = await wait_for(
            lambda: node_b.blockchain.height() == node_a.blockchain.height(),
            timeout=5.0,
        )
        assert synced, (
            f"B height={node_b.blockchain.height()}, "
            f"A height={node_a.blockchain.height()}"
        )
    finally:
        await node_a.stop()
        await node_b.stop()


# ---------------------------------------------------------------------------
# Test 4: invalid signature rejected — not propagated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_transaction_rejected(keypair, tmp_path):
    """Transaction with a bad signature is rejected at the mempool."""
    private_hex, public_hex = keypair
    node_a = await make_node(17706, str(tmp_path))
    node_b = await make_node(17707, str(tmp_path))

    try:
        await node_b.connect_to_peer("127.0.0.1", 17706)
        await asyncio.sleep(0.1)

        tx = make_tx(private_hex, public_hex, "f" * 40)
        tx.signature = "00" * 64   # corrupt signature

        accepted = await node_a.submit_transaction(tx)
        assert not accepted
        assert not node_a.mempool.contains("f" * 40)

        # Give A time to (not) propagate — then verify B also has nothing
        await asyncio.sleep(0.2)
        assert not node_b.mempool.contains("f" * 40)
    finally:
        await node_a.stop()
        await node_b.stop()


# ---------------------------------------------------------------------------
# Test 5: offline node re-syncs to longer chain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resync_after_reconnect(keypair, tmp_path):
    """Node C, after going offline, adopts A's longer chain on reconnect."""
    private_hex, public_hex = keypair
    node_a = await make_node(17708, str(tmp_path))
    node_c = await make_node(17709, str(tmp_path))

    try:
        await node_c.connect_to_peer("127.0.0.1", 17708)
        await asyncio.sleep(0.1)

        # Take C offline
        await node_c.stop()

        # Mine 2 blocks on A while C is gone (manually — deterministic)
        for letter in ["d", "e"]:
            tx = make_tx(private_hex, public_hex, letter * 40)
            block = mine_block(
                [tx.to_dict()],
                node_a.blockchain.tip.hash,
                index=node_a.blockchain.height(),
                difficulty=DIFFICULTY,
            )
            node_a.blockchain.append_block(block)

        height_a = node_a.blockchain.height()
        assert height_a >= 3   # genesis + 2 blocks

        # Bring a fresh node online on a new port (simulating C reconnecting)
        node_c2 = await make_node(17710, str(tmp_path))
        await node_c2.connect_to_peer("127.0.0.1", 17708)

        synced = await wait_for(
            lambda: node_c2.blockchain.height() == height_a,
            timeout=5.0,
        )
        assert synced, (
            f"C2 height={node_c2.blockchain.height()}, expected {height_a}"
        )
        await node_c2.stop()
    finally:
        await node_a.stop()
