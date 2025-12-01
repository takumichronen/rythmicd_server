"""
Block indexer for Miqrochain.

Handles syncing blocks from miqrod, parsing transactions, and updating
the database with blocks, transactions, UTXOs, and scripthash history.
"""

import asyncio
from typing import Any, Callable, Coroutine, Optional

from rythmicd.config.settings import IndexerConfig
from rythmicd.db.dal import DAL
from rythmicd.db.models import Block, Header, Transaction, UTXO, ScripthashHistory
from rythmicd.headers.header_store import HeaderStore, serialize_header
from rythmicd.indexer.reorg_handler import ReorgHandler
from rythmicd.logging import get_logger
from rythmicd.rpc.miq_rpc_client import MiqRPCClient
from rythmicd.scripthash import pkh_to_scripthash_hex

logger = get_logger(__name__)

# Type for notification callbacks
NotifyCallback = Callable[[int, str, set[str]], Coroutine[Any, Any, None]]


def pkh_to_scripthash_hex(pkh_hex: str) -> str:
    """
    Convert PKH hex to scripthash hex.

    Imported from scripthash module but redefined here for clarity.
    """
    from rythmicd.scripthash.scripthash_utils import pkh_hex_to_scripthash_hex
    return pkh_hex_to_scripthash_hex(pkh_hex)


class BlockIndexer:
    """
    Indexes Miqrochain blocks into PostgreSQL.

    Handles initial sync, continuous polling for new blocks,
    and coordinates with ReorgHandler for chain reorganizations.
    """

    def __init__(
        self,
        rpc: MiqRPCClient,
        dal: DAL,
        header_store: HeaderStore,
        config: IndexerConfig,
    ):
        """
        Initialize block indexer.

        Args:
            rpc: RPC client for miqrod
            dal: Data access layer
            header_store: Header store for SPV
            config: Indexer configuration
        """
        self._rpc = rpc
        self._dal = dal
        self._header_store = header_store
        self._config = config
        self._reorg_handler = ReorgHandler(rpc, dal, header_store, config.reorg_limit)

        self._running = False
        self._syncing = False
        self._indexed_height = -1
        self._node_height = -1

        # Callback for notifying protocol layer of new blocks
        self._notify_callback: Optional[NotifyCallback] = None

    def set_notify_callback(self, callback: NotifyCallback) -> None:
        """Set callback for block notifications."""
        self._notify_callback = callback

    @property
    def indexed_height(self) -> int:
        """Get current indexed height."""
        return self._indexed_height

    @property
    def node_height(self) -> int:
        """Get current node height."""
        return self._node_height

    @property
    def is_synced(self) -> bool:
        """Check if indexer is synced with node."""
        return self._indexed_height >= self._node_height

    async def start(self) -> None:
        """Start the block indexer."""
        self._running = True

        # Get current indexed height from database
        self._indexed_height = await self._dal.get_indexed_height()
        logger.info("Block indexer starting", indexed_height=self._indexed_height)

        # Load header cache
        await self._header_store.load_cache()

        # Start main sync loop
        asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        """Stop the block indexer."""
        self._running = False
        logger.info("Block indexer stopped")

    async def _sync_loop(self) -> None:
        """Main synchronization loop."""
        while self._running:
            try:
                # Get current node height
                self._node_height = await self._rpc.get_block_count()

                # Check for reorgs
                if self._indexed_height >= 0:
                    reorg_height = await self._reorg_handler.check_for_reorg()
                    if reorg_height is not None:
                        logger.warning(
                            "Reorg detected",
                            current_height=self._indexed_height,
                            reorg_to=reorg_height,
                        )
                        affected = await self._reorg_handler.handle_reorg(reorg_height)
                        self._indexed_height = reorg_height

                        # Notify subscribers of affected scripthashes
                        if self._notify_callback and affected:
                            await self._notify_callback(reorg_height, "", affected)

                # Sync new blocks
                if self._indexed_height < self._node_height:
                    await self._sync_blocks()
                else:
                    # Fully synced, wait before next poll
                    await asyncio.sleep(self._config.poll_interval)

            except Exception as e:
                logger.error("Sync loop error", error=str(e))
                await asyncio.sleep(self._config.poll_interval)

    async def _sync_blocks(self) -> None:
        """Sync blocks from node."""
        self._syncing = True
        start_height = self._indexed_height + 1
        end_height = min(
            self._node_height, start_height + self._config.batch_size - 1
        )

        logger.info(
            "Syncing blocks",
            start=start_height,
            end=end_height,
            node_height=self._node_height,
        )

        for height in range(start_height, end_height + 1):
            try:
                affected = await self._index_block(height)

                self._indexed_height = height

                # Notify subscribers
                if self._notify_callback:
                    block_hash = (await self._dal.get_block_by_height(height)).hash
                    await self._notify_callback(height, block_hash, affected)

                # Progress logging
                if height % 1000 == 0:
                    logger.info(
                        "Sync progress",
                        height=height,
                        node_height=self._node_height,
                        percent=f"{height / self._node_height * 100:.1f}%",
                    )

            except Exception as e:
                logger.error("Error indexing block", height=height, error=str(e))
                raise

        self._syncing = False

    async def _index_block(self, height: int) -> set[str]:
        """
        Index a single block.

        Returns set of affected scripthashes.
        """
        # Fetch block from node
        block_hash = await self._rpc.get_block_hash(height)
        block_data = await self._rpc.get_block(block_hash)

        # Parse block header
        block = Block(
            height=height,
            hash=block_data["hash"],
            prev_hash=block_data.get("prev_hash", "0" * 64),
            merkle_root=block_data.get("merkle_root", "0" * 64),
            timestamp=block_data["time"],
            version=block_data.get("version", 1),
            bits=block_data.get("bits", 0),
            nonce=block_data.get("nonce", 0),
            tx_count=len(block_data.get("txs", [])),
            size=len(block_data.get("hex", "")) // 2,
        )

        # Serialize header
        header_bytes = serialize_header(
            block.version,
            block.prev_hash,
            block.merkle_root,
            block.timestamp,
            block.bits,
            block.nonce,
        )

        header = Header(
            height=height,
            hash=block.hash,
            prev_hash=block.prev_hash,
            header_bytes=header_bytes,
        )

        # Parse transactions
        transactions: list[Transaction] = []
        utxos_created: list[UTXO] = []
        utxos_spent: list[tuple[str, int, str, int]] = []
        history_entries: list[ScripthashHistory] = []
        affected_scripthashes: set[str] = set()

        for tx_idx, tx_data in enumerate(block_data.get("txs", [])):
            tx, tx_utxos, tx_spends, tx_history = await self._parse_transaction(
                tx_data, block.hash, height, tx_idx
            )
            transactions.append(tx)
            utxos_created.extend(tx_utxos)
            utxos_spent.extend(tx_spends)
            history_entries.extend(tx_history)

            # Track affected scripthashes
            for utxo in tx_utxos:
                affected_scripthashes.add(utxo.scripthash)
            for entry in tx_history:
                affected_scripthashes.add(entry.scripthash)

        # Batch insert to database
        await self._dal.index_block_batch(
            block=block,
            header=header,
            transactions=transactions,
            utxos_created=utxos_created,
            utxos_spent=utxos_spent,
            history_entries=history_entries,
        )

        # Update header store cache
        self._header_store.add_to_cache(header)

        return affected_scripthashes

    async def _parse_transaction(
        self,
        tx_data: dict[str, Any],
        block_hash: str,
        height: int,
        tx_idx: int,
    ) -> tuple[Transaction, list[UTXO], list[tuple[str, int, str, int]], list[ScripthashHistory]]:
        """
        Parse a transaction from RPC data.

        Returns:
            Tuple of (Transaction, UTXOs created, UTXOs spent, history entries)
        """
        txid = tx_data["txid"] if "txid" in tx_data else tx_data.get("hash", "")

        # Handle both raw hex and decoded formats
        raw_tx = tx_data.get("hex", "")

        # Check if coinbase
        is_coinbase = tx_idx == 0

        # Get transaction details if needed
        if "vin" not in tx_data or "vout" not in tx_data:
            # Fetch full transaction details
            try:
                tx_info = await self._rpc.get_transaction_info(txid)
                tx_data = {**tx_data, **tx_info}
            except Exception:
                # Fall back to getrawtransaction
                tx_info = await self._rpc.get_raw_transaction(txid)
                tx_data = {**tx_data, **tx_info}
                raw_tx = tx_info.get("hex", raw_tx)

        utxos_created: list[UTXO] = []
        utxos_spent: list[tuple[str, int, str, int]] = []
        history_entries: list[ScripthashHistory] = []
        total_input = 0
        total_output = 0

        # Process outputs (vout)
        for vout_idx, vout in enumerate(tx_data.get("vout", [])):
            # Handle different output formats
            value = vout.get("value", 0)
            if isinstance(value, float):
                # Convert MIQ to miqrons
                value = int(value * 100_000_000)

            # Get PKH - MIQ stores outputs with bare PKH
            pkh = vout.get("pkh", "")
            if not pkh and "scriptPubKey" in vout:
                # Try to extract from scriptPubKey
                spk = vout["scriptPubKey"]
                if isinstance(spk, dict):
                    pkh = spk.get("addresses", [""])[0] if "addresses" in spk else ""
                    # Or extract hash from asm
                    if not pkh and "asm" in spk:
                        parts = spk["asm"].split()
                        if len(parts) >= 3:
                            pkh = parts[2]

            if not pkh:
                # Skip outputs we can't parse (like OP_RETURN)
                continue

            # Compute scripthash
            try:
                scripthash = pkh_to_scripthash_hex(pkh)
            except Exception:
                continue

            total_output += value

            # Create UTXO
            utxo = UTXO(
                scripthash=scripthash,
                txid=txid,
                vout=vout_idx,
                value=value,
                height=height,
                pkh=pkh,
                is_coinbase=is_coinbase,
                is_spent=False,
            )
            utxos_created.append(utxo)

            # Create history entry for output
            history = ScripthashHistory(
                scripthash=scripthash,
                txid=txid,
                height=height,
                tx_pos=tx_idx * 1000 + vout_idx,  # Ordering within block
                value_delta=value,
            )
            history_entries.append(history)

        # Process inputs (vin) - skip for coinbase
        if not is_coinbase:
            for vin_idx, vin in enumerate(tx_data.get("vin", [])):
                prev_txid = vin.get("txid", vin.get("prev", {}).get("txid", ""))
                prev_vout = vin.get("vout", vin.get("prev", {}).get("vout", 0))

                if not prev_txid:
                    continue

                # Look up the UTXO being spent
                spent_utxo = await self._dal.get_utxo(prev_txid, prev_vout)
                if spent_utxo:
                    utxos_spent.append((prev_txid, prev_vout, txid, height))
                    total_input += spent_utxo.value

                    # Create history entry for spend
                    history = ScripthashHistory(
                        scripthash=spent_utxo.scripthash,
                        txid=txid,
                        height=height,
                        tx_pos=tx_idx * 1000 + 500 + vin_idx,  # Spends after outputs
                        value_delta=-spent_utxo.value,
                    )
                    history_entries.append(history)

        # Calculate fee (inputs - outputs, 0 for coinbase)
        fee = max(0, total_input - total_output) if not is_coinbase else 0

        # Create transaction record
        tx = Transaction(
            txid=txid,
            block_hash=block_hash,
            block_height=height,
            index_in_block=tx_idx,
            raw_tx=raw_tx,
            fee=fee,
            size=len(raw_tx) // 2 if raw_tx else 0,
            is_coinbase=is_coinbase,
            version=tx_data.get("version", 1),
            locktime=tx_data.get("locktime", 0),
        )

        return tx, utxos_created, utxos_spent, history_entries
