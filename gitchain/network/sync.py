"""
Chain synchronisation logic.

When a node connects to a peer for the first time it sends GET_CHAIN
and receives the peer's full chain. This module handles:

  - Requesting the chain from a peer
  - Applying the longest-chain rule against the local chain
  - Handling incoming NEW_BLOCK messages (validate + append or re-sync)
"""

import asyncio
import logging
from typing import List, Optional

from gitchain.core.block import Block
from gitchain.core.blockchain import Blockchain
from gitchain.network.messages import MessageType, encode

log = logging.getLogger(__name__)


async def request_chain(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Send a GET_CHAIN request to an already-open peer connection."""
    writer.write(encode(MessageType.GET_CHAIN))
    await writer.drain()


async def handle_chain_response(
    payload: dict,
    blockchain: Blockchain,
) -> bool:
    """
    Process a CHAIN_RESPONSE payload received from a peer.

    Deserializes the peer's chain, then applies resolve_conflict:
    if the peer's chain is longer and valid, replace local chain.

    Returns True if the local chain was replaced.
    """
    raw_chain = payload.get("chain", [])
    if not raw_chain:
        return False

    peer_chain = [Block.from_dict(b) for b in raw_chain]
    replaced, reason = blockchain.replace_chain(peer_chain)
    if replaced:
        log.info("sync: adopted peer chain (height=%d)", len(peer_chain))
    else:
        log.debug("sync: kept local chain — %s", reason)
    return replaced


async def handle_new_block(
    payload: dict,
    blockchain: Blockchain,
    broadcast_fn,          # async callable(msg_type, payload, exclude_writer)
    writer: asyncio.StreamWriter,
) -> bool:
    """
    Process a NEW_BLOCK message from a peer.

    1. Deserialize the block.
    2. Try to append it to the local chain.
    3. If it fits, re-broadcast to all other peers (gossip propagation).
    4. If it doesn't fit (fork / behind), request the full chain to re-sync.

    Returns True if the block was accepted.
    """
    block = Block.from_dict(payload)
    ok, reason = blockchain.append_block(block)

    if ok:
        log.info("sync: appended block #%d (hash=%s…)", block.index, block.hash[:12])
        # Gossip the block onward to all other peers
        await broadcast_fn(
            MessageType.NEW_BLOCK,
            block.to_dict(),
            exclude_writer=writer,
        )
        return True
    else:
        log.warning("sync: block #%d rejected (%s) — requesting full chain", block.index, reason)
        writer.write(encode(MessageType.GET_CHAIN))
        await writer.drain()
        return False
