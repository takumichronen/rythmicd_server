"""
Blockchain reorganization handler.

Detects and handles chain reorganizations by:
1. Comparing local tip with node's best block
2. Walking back to find common ancestor
3. Rolling back orphaned blocks
4. Triggering re-indexing of new chain
"""

from typing import Optional

from rythmicd.db.dal import DAL
from rythmicd.headers.header_store import HeaderStore
from rythmicd.logging import get_logger
from rythmicd.rpc.miq_rpc_client import MiqRPCClient

logger = get_logger(__name__)


class ReorgHandler:
    """
    Handles blockchain reorganizations.

    Detects when the node's best chain differs from our indexed chain
    and performs rollback operations to resync.
    """

    def __init__(
        self,
        rpc: MiqRPCClient,
        dal: DAL,
        header_store: HeaderStore,
        max_reorg_depth: int = 100,
    ):
        """
        Initialize reorg handler.

        Args:
            rpc: RPC client for miqrod
            dal: Data access layer
            header_store: Header store
            max_reorg_depth: Maximum reorg depth to handle
        """
        self._rpc = rpc
        self._dal = dal
        self._header_store = header_store
        self._max_reorg_depth = max_reorg_depth

    async def check_for_reorg(self) -> Optional[int]:
        """
        Check if a reorg has occurred.

        Compares our tip block hash with the node's block at the same height.
        If they differ, walks back to find the common ancestor.

        Returns:
            Height of common ancestor if reorg detected, None otherwise
        """
        # Get our current tip
        tip_block = await self._dal.get_tip_block()
        if not tip_block:
            return None  # No blocks indexed yet

        # Get node's block at our tip height
        try:
            node_hash = await self._rpc.get_block_hash(tip_block.height)
        except Exception:
            # Height doesn't exist on node - major reorg
            logger.warning(
                "Node height lower than indexed",
                indexed_height=tip_block.height,
            )
            return await self._find_common_ancestor(tip_block.height)

        # Compare hashes
        if node_hash == tip_block.hash:
            return None  # No reorg

        logger.info(
            "Hash mismatch detected",
            height=tip_block.height,
            local_hash=tip_block.hash[:16],
            node_hash=node_hash[:16],
        )

        return await self._find_common_ancestor(tip_block.height)

    async def _find_common_ancestor(self, start_height: int) -> int:
        """
        Find the common ancestor between our chain and the node's chain.

        Args:
            start_height: Height to start searching from

        Returns:
            Height of common ancestor
        """
        height = start_height

        for _ in range(self._max_reorg_depth):
            if height < 0:
                # Genesis reorg - very bad
                logger.error("Reorg extends to genesis!")
                return -1

            # Get our block at this height
            our_block = await self._dal.get_block_by_height(height)
            if not our_block:
                height -= 1
                continue

            # Get node's hash at this height
            try:
                node_hash = await self._rpc.get_block_hash(height)
            except Exception:
                height -= 1
                continue

            if node_hash == our_block.hash:
                logger.info(
                    "Common ancestor found",
                    height=height,
                    hash=our_block.hash[:16],
                )
                return height

            height -= 1

        # Exceeded max reorg depth
        logger.error(
            "Reorg exceeds maximum depth",
            max_depth=self._max_reorg_depth,
        )
        raise RuntimeError(f"Reorg exceeds maximum depth of {self._max_reorg_depth}")

    async def handle_reorg(self, common_ancestor_height: int) -> set[str]:
        """
        Handle a detected reorganization.

        Rolls back all blocks above the common ancestor.

        Args:
            common_ancestor_height: Height of the common ancestor

        Returns:
            Set of affected scripthashes
        """
        # Get current tip height
        tip_block = await self._dal.get_tip_block()
        if not tip_block:
            return set()

        logger.info(
            "Rolling back blocks",
            from_height=tip_block.height,
            to_height=common_ancestor_height,
        )

        all_affected: set[str] = set()

        # Roll back blocks from tip down to common ancestor
        for height in range(tip_block.height, common_ancestor_height, -1):
            affected = await self._rollback_block(height)
            all_affected.update(affected)

        logger.info(
            "Rollback complete",
            blocks_rolled_back=tip_block.height - common_ancestor_height,
            affected_scripthashes=len(all_affected),
        )

        return all_affected

    async def _rollback_block(self, height: int) -> set[str]:
        """
        Roll back a single block.

        Args:
            height: Block height to roll back

        Returns:
            Set of affected scripthashes
        """
        logger.debug("Rolling back block", height=height)

        # Use DAL's rollback method
        affected = await self._dal.rollback_block(height)

        # Remove from header cache
        self._header_store.remove_from_cache(height)

        return affected
