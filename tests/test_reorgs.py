"""
Tests for blockchain reorganization handling.

Tests the ReorgHandler's ability to detect and handle chain reorgs.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from rythmicd.indexer.reorg_handler import ReorgHandler
from rythmicd.db.models import Block


class TestReorgDetection:
    """Test reorg detection logic."""

    @pytest.fixture
    def mock_rpc(self):
        """Create mock RPC client."""
        return AsyncMock()

    @pytest.fixture
    def mock_dal(self):
        """Create mock DAL."""
        dal = AsyncMock()
        return dal

    @pytest.fixture
    def mock_header_store(self):
        """Create mock header store."""
        store = MagicMock()
        store.remove_from_cache = MagicMock()
        return store

    @pytest.mark.asyncio
    async def test_no_reorg_when_hashes_match(self, mock_rpc, mock_dal, mock_header_store):
        """No reorg detected when local and node hashes match."""
        # Set up: local tip matches node tip
        mock_dal.get_tip_block = AsyncMock(return_value=Block(
            height=100,
            hash="ab" * 32,
            prev_hash="cd" * 32,
            merkle_root="ef" * 32,
            timestamp=1700000000,
            version=1,
            bits=0x1d00ffff,
            nonce=12345,
            tx_count=5,
        ))
        mock_rpc.get_block_hash = AsyncMock(return_value="ab" * 32)

        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=100)
        result = await handler.check_for_reorg()

        assert result is None  # No reorg

    @pytest.mark.asyncio
    async def test_reorg_detected_when_hashes_differ(self, mock_rpc, mock_dal, mock_header_store):
        """Reorg detected when local and node hashes differ."""
        # Set up: local tip at height 100 with hash "ab..."
        # Node has different hash at height 100, but same at height 99
        local_blocks = {
            100: Block(
                height=100,
                hash="ab" * 32,
                prev_hash="cd" * 32,
                merkle_root="ef" * 32,
                timestamp=1700000000,
                version=1,
                bits=0x1d00ffff,
                nonce=12345,
                tx_count=5,
            ),
            99: Block(
                height=99,
                hash="cd" * 32,  # This matches node
                prev_hash="ef" * 32,
                merkle_root="12" * 32,
                timestamp=1699999520,
                version=1,
                bits=0x1d00ffff,
                nonce=12344,
                tx_count=3,
            ),
        }

        async def get_tip_block():
            return local_blocks[100]

        async def get_block_by_height(height):
            return local_blocks.get(height)

        async def get_block_hash(height):
            if height == 100:
                return "ff" * 32  # Different from local
            return "cd" * 32  # Same as local at 99

        mock_dal.get_tip_block = get_tip_block
        mock_dal.get_block_by_height = get_block_by_height
        mock_rpc.get_block_hash = get_block_hash

        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=100)
        result = await handler.check_for_reorg()

        assert result == 99  # Common ancestor at height 99

    @pytest.mark.asyncio
    async def test_no_reorg_with_empty_database(self, mock_rpc, mock_dal, mock_header_store):
        """No reorg check needed when database is empty."""
        mock_dal.get_tip_block = AsyncMock(return_value=None)

        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=100)
        result = await handler.check_for_reorg()

        assert result is None


class TestReorgHandling:
    """Test reorg rollback operations."""

    @pytest.fixture
    def mock_rpc(self):
        """Create mock RPC client."""
        return AsyncMock()

    @pytest.fixture
    def mock_dal(self):
        """Create mock DAL."""
        dal = AsyncMock()
        dal.rollback_block = AsyncMock(return_value={"scripthash1", "scripthash2"})
        return dal

    @pytest.fixture
    def mock_header_store(self):
        """Create mock header store."""
        store = MagicMock()
        store.remove_from_cache = MagicMock()
        return store

    @pytest.mark.asyncio
    async def test_handle_single_block_reorg(self, mock_rpc, mock_dal, mock_header_store):
        """Handle reorg of single block."""
        mock_dal.get_tip_block = AsyncMock(return_value=Block(
            height=100,
            hash="ab" * 32,
            prev_hash="cd" * 32,
            merkle_root="ef" * 32,
            timestamp=1700000000,
            version=1,
            bits=0x1d00ffff,
            nonce=12345,
            tx_count=5,
        ))

        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=100)
        affected = await handler.handle_reorg(99)  # Roll back to height 99

        # Should have rolled back block 100
        mock_dal.rollback_block.assert_called_once_with(100)
        mock_header_store.remove_from_cache.assert_called_once_with(100)
        assert affected == {"scripthash1", "scripthash2"}

    @pytest.mark.asyncio
    async def test_handle_multi_block_reorg(self, mock_rpc, mock_dal, mock_header_store):
        """Handle reorg of multiple blocks."""
        mock_dal.get_tip_block = AsyncMock(return_value=Block(
            height=105,
            hash="ab" * 32,
            prev_hash="cd" * 32,
            merkle_root="ef" * 32,
            timestamp=1700000000,
            version=1,
            bits=0x1d00ffff,
            nonce=12345,
            tx_count=5,
        ))

        # Each rollback returns different scripthashes
        mock_dal.rollback_block = AsyncMock(side_effect=[
            {"sh1", "sh2"},  # Block 105
            {"sh2", "sh3"},  # Block 104
            {"sh3", "sh4"},  # Block 103
            {"sh4", "sh5"},  # Block 102
            {"sh5", "sh6"},  # Block 101
        ])

        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=100)
        affected = await handler.handle_reorg(100)  # Roll back to height 100

        # Should have rolled back blocks 105, 104, 103, 102, 101
        assert mock_dal.rollback_block.call_count == 5
        assert mock_header_store.remove_from_cache.call_count == 5

        # All affected scripthashes should be collected
        assert "sh1" in affected
        assert "sh6" in affected


class TestReorgLimits:
    """Test reorg depth limits."""

    @pytest.fixture
    def mock_rpc(self):
        """Create mock RPC client."""
        rpc = AsyncMock()
        # Always return different hash
        rpc.get_block_hash = AsyncMock(return_value="ff" * 32)
        return rpc

    @pytest.fixture
    def mock_dal(self):
        """Create mock DAL."""
        dal = AsyncMock()

        # Return blocks with consistent but different hashes from node
        async def get_block_by_height(height):
            return Block(
                height=height,
                hash=f"{height:064x}",  # Hash based on height
                prev_hash=f"{height-1:064x}",
                merkle_root="ef" * 32,
                timestamp=1700000000,
                version=1,
                bits=0x1d00ffff,
                nonce=12345,
                tx_count=5,
            )

        dal.get_tip_block = AsyncMock(return_value=Block(
            height=100,
            hash="100" + "0" * 61,
            prev_hash="99" + "0" * 62,
            merkle_root="ef" * 32,
            timestamp=1700000000,
            version=1,
            bits=0x1d00ffff,
            nonce=12345,
            tx_count=5,
        ))
        dal.get_block_by_height = get_block_by_height

        return dal

    @pytest.fixture
    def mock_header_store(self):
        """Create mock header store."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_reorg_exceeds_max_depth(self, mock_rpc, mock_dal, mock_header_store):
        """Raise error when reorg exceeds maximum depth."""
        handler = ReorgHandler(mock_rpc, mock_dal, mock_header_store, max_reorg_depth=5)

        with pytest.raises(RuntimeError, match="Reorg exceeds maximum depth"):
            await handler.check_for_reorg()
