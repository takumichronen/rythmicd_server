"""
Mempool tracker for unconfirmed transactions.

Maintains an in-memory representation of the mempool, tracking:
- Unconfirmed transactions
- Per-scripthash unconfirmed balances
- Fee rates for estimation
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from rythmicd.config.settings import IndexerConfig
from rythmicd.db.dal import DAL
from rythmicd.logging import get_logger
from rythmicd.rpc.miq_rpc_client import MiqRPCClient
from rythmicd.scripthash.scripthash_utils import pkh_hex_to_scripthash_hex

logger = get_logger(__name__)

# Callback type for mempool notifications
MempoolNotifyCallback = Callable[[set[str]], Coroutine[Any, Any, None]]


@dataclass
class MempoolEntry:
    """Represents a mempool transaction."""

    txid: str
    raw_tx: str
    fee: int
    size: int
    fee_rate: float  # miqrons per byte
    time_added: int
    inputs: list[tuple[str, int]]  # List of (txid, vout) being spent
    outputs: list[tuple[str, int, int]]  # List of (scripthash, vout, value)


@dataclass
class MempoolState:
    """Current mempool state."""

    # txid -> MempoolEntry
    transactions: dict[str, MempoolEntry] = field(default_factory=dict)

    # scripthash -> list of (txid, delta) for unconfirmed balance
    scripthash_deltas: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    # Fee histogram buckets (fee_rate, vsize)
    fee_histogram: list[tuple[float, int]] = field(default_factory=list)


class MempoolTracker:
    """
    Tracks unconfirmed transactions in the mempool.

    Periodically polls the node for mempool updates and maintains
    an in-memory representation for fast queries.
    """

    def __init__(
        self,
        rpc: MiqRPCClient,
        dal: DAL,
        config: IndexerConfig,
    ):
        """
        Initialize mempool tracker.

        Args:
            rpc: RPC client for miqrod
            dal: Data access layer
            config: Indexer configuration
        """
        self._rpc = rpc
        self._dal = dal
        self._config = config

        self._state = MempoolState()
        self._running = False
        self._notify_callback: Optional[MempoolNotifyCallback] = None

    def set_notify_callback(self, callback: MempoolNotifyCallback) -> None:
        """Set callback for mempool change notifications."""
        self._notify_callback = callback

    @property
    def state(self) -> MempoolState:
        """Get current mempool state."""
        return self._state

    async def start(self) -> None:
        """Start the mempool tracker."""
        self._running = True
        logger.info("Mempool tracker starting")
        asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the mempool tracker."""
        self._running = False
        logger.info("Mempool tracker stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._update_mempool()
            except Exception as e:
                logger.error("Mempool update error", error=str(e))

            await asyncio.sleep(self._config.mempool_poll_interval)

    async def _update_mempool(self) -> None:
        """Update mempool state from node."""
        # Get current mempool txids from node
        try:
            node_txids = set(await self._rpc.get_raw_mempool())
        except Exception as e:
            logger.warning("Failed to get mempool", error=str(e))
            return

        current_txids = set(self._state.transactions.keys())

        # Find new and removed transactions
        new_txids = node_txids - current_txids
        removed_txids = current_txids - node_txids

        affected_scripthashes: set[str] = set()

        # Remove confirmed/dropped transactions
        for txid in removed_txids:
            entry = self._state.transactions.pop(txid, None)
            if entry:
                affected = self._remove_entry_from_deltas(entry)
                affected_scripthashes.update(affected)

        # Add new transactions
        for txid in new_txids:
            try:
                entry = await self._fetch_and_parse_tx(txid)
                if entry:
                    self._state.transactions[txid] = entry
                    affected = self._add_entry_to_deltas(entry)
                    affected_scripthashes.update(affected)
            except Exception as e:
                logger.debug("Failed to parse mempool tx", txid=txid[:16], error=str(e))

        # Update fee histogram
        await self._update_fee_histogram()

        # Notify subscribers
        if affected_scripthashes and self._notify_callback:
            await self._notify_callback(affected_scripthashes)

        if new_txids or removed_txids:
            logger.debug(
                "Mempool updated",
                added=len(new_txids),
                removed=len(removed_txids),
                total=len(self._state.transactions),
            )

    async def _fetch_and_parse_tx(self, txid: str) -> Optional[MempoolEntry]:
        """Fetch and parse a mempool transaction."""
        try:
            tx_data = await self._rpc.get_transaction_info(txid)
        except Exception:
            tx_data = await self._rpc.get_raw_transaction(txid)

        if not tx_data:
            return None

        raw_tx = tx_data.get("hex", "")
        size = tx_data.get("size", len(raw_tx) // 2)
        fee = tx_data.get("fee", 0)

        # Convert fee from MIQ to miqrons if needed
        if isinstance(fee, float):
            fee = int(fee * 100_000_000)

        fee_rate = fee / size if size > 0 else 0

        inputs: list[tuple[str, int]] = []
        outputs: list[tuple[str, int, int]] = []

        # Parse inputs
        for vin in tx_data.get("vin", []):
            prev_txid = vin.get("txid", vin.get("prev", {}).get("txid", ""))
            prev_vout = vin.get("vout", vin.get("prev", {}).get("vout", 0))
            if prev_txid:
                inputs.append((prev_txid, prev_vout))

        # Parse outputs
        for vout_idx, vout in enumerate(tx_data.get("vout", [])):
            value = vout.get("value", 0)
            if isinstance(value, float):
                value = int(value * 100_000_000)

            pkh = vout.get("pkh", "")
            if pkh:
                try:
                    scripthash = pkh_hex_to_scripthash_hex(pkh)
                    outputs.append((scripthash, vout_idx, value))
                except Exception:
                    pass

        return MempoolEntry(
            txid=txid,
            raw_tx=raw_tx,
            fee=fee,
            size=size,
            fee_rate=fee_rate,
            time_added=int(asyncio.get_event_loop().time()),
            inputs=inputs,
            outputs=outputs,
        )

    def _add_entry_to_deltas(self, entry: MempoolEntry) -> set[str]:
        """Add transaction to scripthash deltas."""
        affected: set[str] = set()

        # Add output deltas (positive)
        for scripthash, _, value in entry.outputs:
            if scripthash not in self._state.scripthash_deltas:
                self._state.scripthash_deltas[scripthash] = []
            self._state.scripthash_deltas[scripthash].append((entry.txid, value))
            affected.add(scripthash)

        # Add input deltas (negative) - need to look up spent UTXOs
        # This is async but we're in sync context, so we'll skip for now
        # The proper implementation would cache UTXO scripthashes

        return affected

    def _remove_entry_from_deltas(self, entry: MempoolEntry) -> set[str]:
        """Remove transaction from scripthash deltas."""
        affected: set[str] = set()

        for scripthash, _, _ in entry.outputs:
            if scripthash in self._state.scripthash_deltas:
                self._state.scripthash_deltas[scripthash] = [
                    (txid, delta)
                    for txid, delta in self._state.scripthash_deltas[scripthash]
                    if txid != entry.txid
                ]
                if not self._state.scripthash_deltas[scripthash]:
                    del self._state.scripthash_deltas[scripthash]
                affected.add(scripthash)

        return affected

    async def _update_fee_histogram(self) -> None:
        """Update fee histogram from mempool."""
        if not self._state.transactions:
            self._state.fee_histogram = []
            return

        # Build histogram buckets
        fee_rates = [
            (entry.fee_rate, entry.size)
            for entry in self._state.transactions.values()
        ]

        # Sort by fee rate descending
        fee_rates.sort(key=lambda x: x[0], reverse=True)

        # Create buckets (simplified)
        buckets: list[tuple[float, int]] = []
        current_rate = 0.0
        current_size = 0

        for rate, size in fee_rates:
            if buckets and abs(rate - current_rate) < 0.5:
                # Same bucket
                current_size += size
                buckets[-1] = (current_rate, current_size)
            else:
                # New bucket
                current_rate = rate
                current_size = size
                buckets.append((current_rate, current_size))

        self._state.fee_histogram = buckets[:20]  # Top 20 buckets

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_mempool_txids(self) -> list[str]:
        """Get all mempool transaction IDs."""
        return list(self._state.transactions.keys())

    def get_transaction(self, txid: str) -> Optional[MempoolEntry]:
        """Get a specific mempool transaction."""
        return self._state.transactions.get(txid)

    def get_scripthash_mempool(self, scripthash: str) -> list[dict[str, Any]]:
        """
        Get mempool entries for a scripthash.

        Returns list of {tx_hash, height, fee} dicts.
        """
        result = []

        for txid, entry in self._state.transactions.items():
            # Check if this scripthash is involved
            for sh, _, _ in entry.outputs:
                if sh == scripthash:
                    result.append({
                        "tx_hash": txid,
                        "height": 0,  # 0 indicates mempool
                        "fee": entry.fee,
                    })
                    break

        return result

    def get_scripthash_unconfirmed_balance(self, scripthash: str) -> int:
        """Get unconfirmed balance delta for a scripthash."""
        if scripthash not in self._state.scripthash_deltas:
            return 0

        return sum(delta for _, delta in self._state.scripthash_deltas[scripthash])

    def get_fee_histogram(self) -> list[list[float | int]]:
        """Get fee histogram for fee estimation."""
        return [[rate, size] for rate, size in self._state.fee_histogram]

    def get_stats(self) -> dict[str, Any]:
        """Get mempool statistics."""
        if not self._state.transactions:
            return {
                "size": 0,
                "bytes": 0,
                "min_fee_rate": 0,
                "max_fee_rate": 0,
                "avg_fee_rate": 0,
            }

        sizes = [e.size for e in self._state.transactions.values()]
        fee_rates = [e.fee_rate for e in self._state.transactions.values()]

        return {
            "size": len(self._state.transactions),
            "bytes": sum(sizes),
            "min_fee_rate": min(fee_rates) if fee_rates else 0,
            "max_fee_rate": max(fee_rates) if fee_rates else 0,
            "avg_fee_rate": sum(fee_rates) / len(fee_rates) if fee_rates else 0,
        }
