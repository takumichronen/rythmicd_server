"""
Tests for mempool tracking functionality.

Tests MempoolTracker's ability to track unconfirmed transactions
and maintain scripthash deltas.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from rythmicd.config.settings import IndexerConfig
from rythmicd.indexer.mempool_tracker import MempoolTracker, MempoolEntry, MempoolState


class TestMempoolEntry:
    """Test MempoolEntry model."""

    def test_entry_creation(self):
        """Create a mempool entry."""
        entry = MempoolEntry(
            txid="ab" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[("cd" * 32, 0)],
            outputs=[("ef" * 32, 0, 5000000000)],
        )

        assert entry.txid == "ab" * 32
        assert entry.fee == 1000
        assert entry.fee_rate == 4.0
        assert len(entry.inputs) == 1
        assert len(entry.outputs) == 1


class TestMempoolState:
    """Test MempoolState container."""

    def test_empty_state(self):
        """New state is empty."""
        state = MempoolState()

        assert len(state.transactions) == 0
        assert len(state.scripthash_deltas) == 0
        assert len(state.fee_histogram) == 0

    def test_add_transaction(self):
        """Add transaction to state."""
        state = MempoolState()

        entry = MempoolEntry(
            txid="ab" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[("ef" * 32, 0, 5000000000)],
        )

        state.transactions["ab" * 32] = entry
        assert "ab" * 32 in state.transactions


@pytest.fixture
def mock_rpc():
    """Create mock RPC client."""
    rpc = AsyncMock()
    rpc.get_raw_mempool = AsyncMock(return_value=[])
    rpc.get_transaction_info = AsyncMock(return_value={
        "hex": "0100...",
        "size": 250,
        "fee": 1000,
        "vin": [],
        "vout": [],
    })
    return rpc


@pytest.fixture
def mock_dal():
    """Create mock DAL."""
    return AsyncMock()


@pytest.fixture
def config():
    """Create test config."""
    return IndexerConfig(mempool_poll_interval=5.0)


@pytest.fixture
def tracker(mock_rpc, mock_dal, config):
    """Create MempoolTracker with mocked dependencies."""
    return MempoolTracker(mock_rpc, mock_dal, config)


class TestMempoolTracker:
    """Test MempoolTracker functionality."""

    def test_initial_state(self, tracker):
        """Tracker starts with empty state."""
        assert len(tracker.state.transactions) == 0
        assert tracker.get_mempool_txids() == []

    def test_get_stats_empty(self, tracker):
        """Stats for empty mempool."""
        stats = tracker.get_stats()

        assert stats["size"] == 0
        assert stats["bytes"] == 0

    def test_get_scripthash_mempool_empty(self, tracker):
        """No mempool txs for scripthash."""
        result = tracker.get_scripthash_mempool("ab" * 32)
        assert result == []

    def test_get_scripthash_unconfirmed_balance_empty(self, tracker):
        """No unconfirmed balance for scripthash."""
        result = tracker.get_scripthash_unconfirmed_balance("ab" * 32)
        assert result == 0

    def test_get_fee_histogram_empty(self, tracker):
        """Empty fee histogram."""
        result = tracker.get_fee_histogram()
        assert result == []


class TestMempoolDeltaTracking:
    """Test scripthash delta tracking."""

    def test_add_entry_to_deltas(self, tracker):
        """Adding entry updates scripthash deltas."""
        entry = MempoolEntry(
            txid="ab" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[("scripthash1" + "0" * 50, 0, 5000000000)],
        )

        affected = tracker._add_entry_to_deltas(entry)

        assert "scripthash1" + "0" * 50 in affected
        assert "scripthash1" + "0" * 50 in tracker.state.scripthash_deltas

    def test_remove_entry_from_deltas(self, tracker):
        """Removing entry clears scripthash deltas."""
        scripthash = "scripthash1" + "0" * 50
        entry = MempoolEntry(
            txid="ab" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[(scripthash, 0, 5000000000)],
        )

        # Add then remove
        tracker._add_entry_to_deltas(entry)
        tracker.state.transactions["ab" * 32] = entry

        affected = tracker._remove_entry_from_deltas(entry)

        assert scripthash in affected
        # Deltas should be empty after removal
        assert scripthash not in tracker.state.scripthash_deltas

    def test_unconfirmed_balance_calculation(self, tracker):
        """Calculate unconfirmed balance from deltas."""
        scripthash = "ab" * 32

        # Manually add deltas
        tracker.state.scripthash_deltas[scripthash] = [
            ("tx1", 1000000),
            ("tx2", 2000000),
        ]

        result = tracker.get_scripthash_unconfirmed_balance(scripthash)
        assert result == 3000000

    def test_multiple_outputs_same_scripthash(self, tracker):
        """Handle multiple outputs to same scripthash."""
        scripthash = "ab" * 32
        entry = MempoolEntry(
            txid="tx1" + "0" * 60,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[
                (scripthash, 0, 1000000),
                (scripthash, 1, 2000000),
            ],
        )

        tracker._add_entry_to_deltas(entry)

        # Both outputs should be tracked
        deltas = tracker.state.scripthash_deltas[scripthash]
        assert len(deltas) == 2


class TestMempoolQueries:
    """Test mempool query methods."""

    def test_get_transaction_found(self, tracker):
        """Get existing mempool transaction."""
        entry = MempoolEntry(
            txid="ab" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[],
        )
        tracker.state.transactions["ab" * 32] = entry

        result = tracker.get_transaction("ab" * 32)
        assert result == entry

    def test_get_transaction_not_found(self, tracker):
        """Get non-existent mempool transaction."""
        result = tracker.get_transaction("ab" * 32)
        assert result is None

    def test_get_scripthash_mempool_with_entries(self, tracker):
        """Get mempool entries for scripthash."""
        scripthash = "ab" * 32
        entry = MempoolEntry(
            txid="cd" * 32,
            raw_tx="0100...",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=1700000000,
            inputs=[],
            outputs=[(scripthash, 0, 5000000000)],
        )
        tracker.state.transactions["cd" * 32] = entry

        result = tracker.get_scripthash_mempool(scripthash)

        assert len(result) == 1
        assert result[0]["tx_hash"] == "cd" * 32
        assert result[0]["height"] == 0  # Mempool indicator

    def test_get_mempool_txids(self, tracker):
        """Get all mempool transaction IDs."""
        tracker.state.transactions["tx1" + "0" * 60] = MagicMock()
        tracker.state.transactions["tx2" + "0" * 60] = MagicMock()

        result = tracker.get_mempool_txids()

        assert len(result) == 2
        assert "tx1" + "0" * 60 in result
        assert "tx2" + "0" * 60 in result


class TestMempoolStats:
    """Test mempool statistics."""

    def test_stats_with_transactions(self, tracker):
        """Calculate stats with transactions."""
        tracker.state.transactions["tx1"] = MempoolEntry(
            txid="tx1",
            raw_tx="",
            fee=1000,
            size=250,
            fee_rate=4.0,
            time_added=0,
            inputs=[],
            outputs=[],
        )
        tracker.state.transactions["tx2"] = MempoolEntry(
            txid="tx2",
            raw_tx="",
            fee=2000,
            size=500,
            fee_rate=4.0,
            time_added=0,
            inputs=[],
            outputs=[],
        )

        stats = tracker.get_stats()

        assert stats["size"] == 2
        assert stats["bytes"] == 750
        assert stats["min_fee_rate"] == 4.0
        assert stats["max_fee_rate"] == 4.0
        assert stats["avg_fee_rate"] == 4.0
