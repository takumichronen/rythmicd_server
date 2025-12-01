"""
Tests for block indexer logic.

Uses mocked RPC and in-memory structures to test indexing behavior.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from rythmicd.config.settings import IndexerConfig
from rythmicd.db.models import Block, Header, Transaction, UTXO, ScripthashHistory
from rythmicd.headers.header_store import serialize_header, deserialize_header, MIQ_HEADER_SIZE


class TestHeaderSerialization:
    """Test block header serialization."""

    def test_serialize_header_length(self):
        """Serialized header should be 88 bytes."""
        header = serialize_header(
            version=1,
            prev_hash="00" * 32,
            merkle_root="00" * 32,
            timestamp=1700000000,
            bits=0x1d00ffff,
            nonce=12345,
        )
        assert len(header) == MIQ_HEADER_SIZE

    def test_serialize_deserialize_roundtrip(self):
        """Header should survive serialize/deserialize roundtrip."""
        original = {
            "version": 1,
            "prev_hash": "ab" * 32,
            "merkle_root": "cd" * 32,
            "timestamp": 1700000000,
            "bits": 0x1d00ffff,
            "nonce": 12345,
        }

        serialized = serialize_header(**original)
        deserialized = deserialize_header(serialized)

        assert deserialized["version"] == original["version"]
        assert deserialized["prev_hash"] == original["prev_hash"]
        assert deserialized["merkle_root"] == original["merkle_root"]
        assert deserialized["timestamp"] == original["timestamp"]
        assert deserialized["bits"] == original["bits"]
        assert deserialized["nonce"] == original["nonce"]

    def test_deserialize_invalid_length(self):
        """Reject headers with wrong length."""
        with pytest.raises(ValueError, match="Invalid header size"):
            deserialize_header(bytes(80))  # Bitcoin header size, not MIQ


class TestBlockModel:
    """Test Block model."""

    def test_block_creation(self):
        """Create a Block with all fields."""
        block = Block(
            height=100,
            hash="ab" * 32,
            prev_hash="cd" * 32,
            merkle_root="ef" * 32,
            timestamp=1700000000,
            version=1,
            bits=0x1d00ffff,
            nonce=12345,
            tx_count=5,
            size=1000,
        )

        assert block.height == 100
        assert block.hash == "ab" * 32
        assert block.tx_count == 5


class TestTransactionModel:
    """Test Transaction model."""

    def test_transaction_creation(self):
        """Create a Transaction with required fields."""
        tx = Transaction(
            txid="ab" * 32,
            block_hash="cd" * 32,
            block_height=100,
            index_in_block=0,
            raw_tx="0100000001...",
            fee=1000,
            size=250,
            is_coinbase=True,
        )

        assert tx.txid == "ab" * 32
        assert tx.is_coinbase is True
        assert tx.fee == 1000


class TestUTXOModel:
    """Test UTXO model."""

    def test_utxo_creation(self):
        """Create a UTXO with all fields."""
        utxo = UTXO(
            scripthash="ab" * 32,
            txid="cd" * 32,
            vout=0,
            value=5000000000,  # 50 MIQ in miqrons
            height=100,
            pkh="00" * 20,
            is_coinbase=True,
            is_spent=False,
        )

        assert utxo.scripthash == "ab" * 32
        assert utxo.value == 5000000000
        assert utxo.is_coinbase is True
        assert utxo.is_spent is False

    def test_utxo_spent_state(self):
        """UTXO can track spent state."""
        utxo = UTXO(
            scripthash="ab" * 32,
            txid="cd" * 32,
            vout=0,
            value=1000,
            height=100,
            pkh="00" * 20,
            is_spent=True,
            spent_by_txid="ef" * 32,
            spent_height=150,
        )

        assert utxo.is_spent is True
        assert utxo.spent_by_txid == "ef" * 32
        assert utxo.spent_height == 150


class TestScripthashHistoryModel:
    """Test ScripthashHistory model."""

    def test_history_entry_output(self):
        """History entry for an output (positive delta)."""
        entry = ScripthashHistory(
            scripthash="ab" * 32,
            txid="cd" * 32,
            height=100,
            tx_pos=0,
            value_delta=5000000000,  # Received 50 MIQ
        )

        assert entry.value_delta > 0

    def test_history_entry_spend(self):
        """History entry for a spend (negative delta)."""
        entry = ScripthashHistory(
            scripthash="ab" * 32,
            txid="cd" * 32,
            height=100,
            tx_pos=1,
            value_delta=-5000000000,  # Spent 50 MIQ
        )

        assert entry.value_delta < 0


class TestIndexerConfig:
    """Test IndexerConfig defaults."""

    def test_default_values(self):
        """IndexerConfig has sensible defaults."""
        config = IndexerConfig()

        assert config.poll_interval == 1.0
        assert config.mempool_poll_interval == 5.0
        assert config.batch_size == 100
        assert config.flush_interval == 1000
        assert config.reorg_limit == 100


class TestMockedIndexer:
    """Test indexer with mocked dependencies."""

    @pytest.fixture
    def mock_rpc(self):
        """Create mock RPC client."""
        rpc = AsyncMock()
        rpc.get_block_count = AsyncMock(return_value=100)
        rpc.get_best_block_hash = AsyncMock(return_value="ab" * 32)
        rpc.get_block_hash = AsyncMock(return_value="ab" * 32)
        rpc.get_block = AsyncMock(return_value={
            "hash": "ab" * 32,
            "prev_hash": "cd" * 32,
            "merkle_root": "ef" * 32,
            "time": 1700000000,
            "version": 1,
            "bits": 0x1d00ffff,
            "nonce": 12345,
            "txs": [],
            "hex": "00" * 100,
        })
        return rpc

    @pytest.fixture
    def mock_dal(self):
        """Create mock DAL."""
        dal = AsyncMock()
        dal.get_indexed_height = AsyncMock(return_value=-1)
        dal.get_tip_block = AsyncMock(return_value=None)
        dal.index_block_batch = AsyncMock()
        return dal

    @pytest.fixture
    def mock_header_store(self):
        """Create mock header store."""
        store = AsyncMock()
        store.load_cache = AsyncMock()
        store.add_to_cache = MagicMock()
        store.tip_height = -1
        return store

    @pytest.mark.asyncio
    async def test_indexer_starts_from_genesis(self, mock_rpc, mock_dal, mock_header_store):
        """Indexer starts from genesis when database is empty."""
        from rythmicd.indexer.block_indexer import BlockIndexer

        config = IndexerConfig()
        indexer = BlockIndexer(mock_rpc, mock_dal, mock_header_store, config)

        # Verify initial state
        assert indexer.indexed_height == -1

        # Start would trigger sync
        mock_dal.get_indexed_height.assert_not_called()  # Not called until start()
