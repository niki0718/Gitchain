"""
Proof-of-Work miner for GitChain.

Difficulty is set low (3 leading zeros) so blocks mine in seconds.
This keeps the system fast for a development team while still making
chain rewrites computationally infeasible when multiple nodes are running.

The miner runs in a background thread inside the node daemon.
"""

import time
from typing import List, Optional, Callable

from gitchain.core.block import Block


def mine_block(
    transactions: List[dict],
    previous_hash: str,
    index: int,
    difficulty: int = 3,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> Block:
    """
    Mine a new block via Proof of Work.

    Increments nonce until the block's SHA-256 hash starts with
    `difficulty` leading zeros.

    Args:
        transactions: list of serialized Transaction dicts
        previous_hash: hash of the current chain tip
        index: position this block will occupy in the chain
        difficulty: number of leading zero hex chars required
        stop_flag: optional callable; if it returns True, mining aborts
                   and returns None (used when a peer finds the block first)

    Returns:
        A fully mined Block with a valid hash, or None if stop_flag triggered.
    """
    target = "0" * difficulty
    timestamp = time.time()

    block = Block(
        index=index,
        timestamp=timestamp,
        transactions=transactions,
        previous_hash=previous_hash,
        nonce=0,
    )

    while True:
        if stop_flag and stop_flag():
            return None

        block.hash = block.calculate_hash()
        if block.hash.startswith(target):
            return block

        block.nonce += 1


def meets_difficulty(hash_str: str, difficulty: int) -> bool:
    """Check whether a given hash satisfies the PoW difficulty target."""
    return hash_str.startswith("0" * difficulty)
