"""
Rythmium protocol method handlers.

Implements the Rythmium JSON-RPC API methods for wallet communication.
"""

from typing import TYPE_CHECKING, Any, Optional

from rythmicd.config.settings import RythmiumServerConfig
from rythmicd.db.dal import DAL
from rythmicd.headers.header_store import HeaderStore
from rythmicd.indexer.mempool_tracker import MempoolTracker
from rythmicd.logging import get_logger
from rythmicd.rpc.miq_rpc_client import MiqRPCClient

if TYPE_CHECKING:
    from rythmicd.protocol.server import ClientSession, RythmiumServer

logger = get_logger(__name__)


class RythmiumHandlers:
    """
    Implements Rythmium protocol method handlers.

    Supports the standard Rythmium methods for blockchain queries,
    scripthash operations, and transaction handling.
    """

    def __init__(
        self,
        rpc: MiqRPCClient,
        dal: DAL,
        header_store: HeaderStore,
        mempool: MempoolTracker,
        config: RythmiumServerConfig,
    ):
        """
        Initialize handlers.

        Args:
            rpc: RPC client for miqrod
            dal: Data access layer
            header_store: Header store for SPV
            mempool: Mempool tracker
            config: Server configuration
        """
        self._rpc = rpc
        self._dal = dal
        self._header_store = header_store
        self._mempool = mempool
        self._config = config
        self._server: Optional["RythmiumServer"] = None

        # Method dispatch table
        self._methods: dict[str, Any] = {
            # Server methods
            "server.version": self.server_version,
            "server.banner": self.server_banner,
            "server.donation_address": self.server_donation_address,
            "server.features": self.server_features,
            "server.ping": self.server_ping,
            # Blockchain methods
            "blockchain.headers.subscribe": self.blockchain_headers_subscribe,
            "blockchain.block.header": self.blockchain_block_header,
            "blockchain.block.headers": self.blockchain_block_headers,
            "blockchain.estimatefee": self.blockchain_estimatefee,
            "blockchain.relayfee": self.blockchain_relayfee,
            # Scripthash methods
            "blockchain.scripthash.subscribe": self.blockchain_scripthash_subscribe,
            "blockchain.scripthash.unsubscribe": self.blockchain_scripthash_unsubscribe,
            "blockchain.scripthash.get_balance": self.blockchain_scripthash_get_balance,
            "blockchain.scripthash.get_history": self.blockchain_scripthash_get_history,
            "blockchain.scripthash.get_mempool": self.blockchain_scripthash_get_mempool,
            "blockchain.scripthash.listunspent": self.blockchain_scripthash_listunspent,
            # Transaction methods
            "blockchain.transaction.get": self.blockchain_transaction_get,
            "blockchain.transaction.broadcast": self.blockchain_transaction_broadcast,
            "blockchain.transaction.get_merkle": self.blockchain_transaction_get_merkle,
            "blockchain.transaction.id_from_pos": self.blockchain_transaction_id_from_pos,
            # Mempool methods
            "mempool.get_fee_histogram": self.mempool_get_fee_histogram,
        }

    def set_server(self, server: "RythmiumServer") -> None:
        """Set server reference for subscriptions."""
        self._server = server

    async def handle(
        self,
        client: "ClientSession",
        method: str,
        params: list[Any] | dict[str, Any],
    ) -> Any:
        """
        Dispatch a method call to the appropriate handler.

        Args:
            client: Client session
            method: Method name
            params: Method parameters

        Returns:
            Method result

        Raises:
            Exception: If method not found or handler fails
        """
        handler = self._methods.get(method)
        if not handler:
            raise Exception(f"Unknown method: {method}")

        # Convert params to list if dict
        if isinstance(params, dict):
            params = list(params.values())

        return await handler(client, params)

    # =========================================================================
    # Server Methods
    # =========================================================================

    async def server_version(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> list[str]:
        """
        Negotiate protocol version.

        Params: [client_name, protocol_version]
        Returns: [server_version, protocol_version]
        """
        client_name = params[0] if params else "unknown"
        requested_version = params[1] if len(params) > 1 else self._config.protocol_min

        # Handle version range
        if isinstance(requested_version, list):
            requested_version = requested_version[0]

        client.client_name = client_name
        client.protocol_version = requested_version

        logger.info(
            "Client version negotiated",
            client_id=client.id[:8],
            client_name=client_name,
            version=requested_version,
        )

        return ["rythmicd 0.1.0", self._config.protocol_max]

    async def server_banner(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str:
        """Get server banner."""
        return self._config.banner

    async def server_donation_address(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str:
        """Get donation address."""
        return self._config.donation_address or ""

    async def server_features(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> dict[str, Any]:
        """Get server features."""
        return {
            "genesis_hash": await self._get_genesis_hash(),
            "hash_function": "sha256",
            "server_version": "rythmicd 0.1.0",
            "protocol_min": self._config.protocol_min,
            "protocol_max": self._config.protocol_max,
            "pruning": None,
        }

    async def server_ping(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> None:
        """Ping the server."""
        return None

    # =========================================================================
    # Blockchain Header Methods
    # =========================================================================

    async def blockchain_headers_subscribe(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> dict[str, Any]:
        """
        Subscribe to new block headers.

        Returns the current tip header.
        """
        client.subscribed_headers = True

        tip_header = await self._header_store.get_tip_header()
        if not tip_header:
            return {"height": 0, "hex": ""}

        return {
            "height": tip_header.height,
            "hex": tip_header.header_bytes.hex(),
        }

    async def blockchain_block_header(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str:
        """
        Get a block header by height.

        Params: [height, cp_height (optional)]
        Returns: header hex string
        """
        height = params[0] if params else 0

        header_hex = await self._header_store.get_header_hex(height)
        if not header_hex:
            raise Exception(f"Block not found at height {height}")

        return header_hex

    async def blockchain_block_headers(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> dict[str, Any]:
        """
        Get a range of block headers.

        Params: [start_height, count, cp_height (optional)]
        Returns: {count, hex, max}
        """
        start_height = params[0] if params else 0
        count = min(params[1] if len(params) > 1 else 1, 2016)

        headers_hex = await self._header_store.get_headers_hex(start_height, count)
        actual_count = len(headers_hex) // 176  # 88 bytes * 2 hex chars

        return {
            "count": actual_count,
            "hex": headers_hex,
            "max": 2016,
        }

    async def blockchain_estimatefee(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> float:
        """
        Estimate fee for confirmation within N blocks.

        Params: [target_blocks]
        Returns: fee rate in MIQ/kB
        """
        # Simple estimation based on mempool
        stats = self._mempool.get_stats()
        avg_rate = stats.get("avg_fee_rate", 1.0)

        # Convert from miqrons/byte to MIQ/kB
        return avg_rate * 1000 / 100_000_000

    async def blockchain_relayfee(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> float:
        """Get minimum relay fee rate."""
        return 0.00001  # 1 miqron/byte in MIQ/kB

    # =========================================================================
    # Scripthash Methods
    # =========================================================================

    async def blockchain_scripthash_subscribe(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> Optional[str]:
        """
        Subscribe to scripthash status changes.

        Params: [scripthash]
        Returns: current status hash or null
        """
        scripthash = params[0] if params else ""

        if not scripthash or len(scripthash) != 64:
            raise Exception("Invalid scripthash")

        # Check subscription limit
        if len(client.subscribed_scripthashes) >= self._config.max_subscriptions_per_client:
            raise Exception("Subscription limit reached")

        client.subscribed_scripthashes.add(scripthash)

        # Return current status
        return await self.get_scripthash_status(scripthash)

    async def blockchain_scripthash_unsubscribe(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> bool:
        """
        Unsubscribe from scripthash.

        Params: [scripthash]
        Returns: True if was subscribed
        """
        scripthash = params[0] if params else ""
        was_subscribed = scripthash in client.subscribed_scripthashes
        client.subscribed_scripthashes.discard(scripthash)
        return was_subscribed

    async def blockchain_scripthash_get_balance(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> dict[str, int]:
        """
        Get balance for a scripthash.

        Params: [scripthash]
        Returns: {confirmed, unconfirmed}
        """
        scripthash = params[0] if params else ""

        if not scripthash or len(scripthash) != 64:
            raise Exception("Invalid scripthash")

        # Get confirmed balance from database
        balance = await self._dal.get_balance_for_scripthash(scripthash)

        # Add unconfirmed from mempool
        unconfirmed = self._mempool.get_scripthash_unconfirmed_balance(scripthash)

        return {
            "confirmed": balance["confirmed"],
            "unconfirmed": unconfirmed,
        }

    async def blockchain_scripthash_get_history(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> list[dict[str, Any]]:
        """
        Get transaction history for a scripthash.

        Params: [scripthash]
        Returns: [{tx_hash, height, fee?}, ...]
        """
        scripthash = params[0] if params else ""

        if not scripthash or len(scripthash) != 64:
            raise Exception("Invalid scripthash")

        # Get confirmed history
        history = await self._dal.get_scripthash_history(scripthash)

        result: list[dict[str, Any]] = []
        seen_txids: set[str] = set()

        for entry in history:
            if entry.txid not in seen_txids:
                result.append({
                    "tx_hash": entry.txid,
                    "height": entry.height,
                })
                seen_txids.add(entry.txid)

        # Add mempool transactions
        mempool_txs = self._mempool.get_scripthash_mempool(scripthash)
        for tx in mempool_txs:
            if tx["tx_hash"] not in seen_txids:
                result.append(tx)
                seen_txids.add(tx["tx_hash"])

        return result

    async def blockchain_scripthash_get_mempool(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> list[dict[str, Any]]:
        """
        Get unconfirmed transactions for a scripthash.

        Params: [scripthash]
        Returns: [{tx_hash, height, fee}, ...]
        """
        scripthash = params[0] if params else ""

        if not scripthash or len(scripthash) != 64:
            raise Exception("Invalid scripthash")

        return self._mempool.get_scripthash_mempool(scripthash)

    async def blockchain_scripthash_listunspent(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> list[dict[str, Any]]:
        """
        Get unspent outputs for a scripthash.

        Params: [scripthash]
        Returns: [{tx_hash, tx_pos, height, value}, ...]
        """
        scripthash = params[0] if params else ""

        if not scripthash or len(scripthash) != 64:
            raise Exception("Invalid scripthash")

        utxos = await self._dal.get_utxos_for_scripthash(scripthash, include_spent=False)

        return [
            {
                "tx_hash": utxo.txid,
                "tx_pos": utxo.vout,
                "height": utxo.height,
                "value": utxo.value,
            }
            for utxo in utxos
        ]

    # =========================================================================
    # Transaction Methods
    # =========================================================================

    async def blockchain_transaction_get(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str | dict[str, Any]:
        """
        Get a transaction by txid.

        Params: [txid, verbose (optional)]
        Returns: raw tx hex or verbose dict
        """
        txid = params[0] if params else ""
        verbose = params[1] if len(params) > 1 else False

        if not txid or len(txid) != 64:
            raise Exception("Invalid txid")

        # Try database first
        tx = await self._dal.get_transaction(txid)
        if tx:
            if verbose:
                return {
                    "txid": tx.txid,
                    "hash": tx.txid,
                    "hex": tx.raw_tx,
                    "blockhash": tx.block_hash,
                    "confirmations": self._header_store.tip_height - tx.block_height + 1,
                }
            return tx.raw_tx

        # Try mempool
        mempool_entry = self._mempool.get_transaction(txid)
        if mempool_entry:
            if verbose:
                return {
                    "txid": txid,
                    "hash": txid,
                    "hex": mempool_entry.raw_tx,
                    "confirmations": 0,
                }
            return mempool_entry.raw_tx

        # Fall back to node RPC
        try:
            tx_data = await self._rpc.get_raw_transaction(txid)
            if verbose:
                return tx_data
            return tx_data.get("hex", "")
        except Exception:
            raise Exception(f"Transaction not found: {txid}")

    async def blockchain_transaction_broadcast(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str:
        """
        Broadcast a raw transaction.

        Params: [raw_tx_hex]
        Returns: txid
        """
        raw_tx = params[0] if params else ""

        if not raw_tx:
            raise Exception("Missing transaction")

        try:
            result = await self._rpc.send_raw_transaction(raw_tx)
            if isinstance(result, dict):
                return result.get("result", result.get("txid", ""))
            return str(result)
        except Exception as e:
            raise Exception(f"Broadcast failed: {str(e)}")

    async def blockchain_transaction_get_merkle(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> dict[str, Any]:
        """
        Get merkle proof for a transaction.

        Params: [txid, height]
        Returns: {merkle, block_height, pos}
        """
        txid = params[0] if params else ""
        height = params[1] if len(params) > 1 else None

        if not txid:
            raise Exception("Missing txid")

        # Get transaction to find block
        tx = await self._dal.get_transaction(txid)
        if not tx:
            raise Exception(f"Transaction not found: {txid}")

        # For now, return empty merkle proof
        # Full implementation would compute actual merkle path
        return {
            "merkle": [],
            "block_height": tx.block_height,
            "pos": tx.index_in_block,
        }

    async def blockchain_transaction_id_from_pos(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> str | dict[str, Any]:
        """
        Get txid at a specific position in a block.

        Params: [height, tx_pos, merkle (optional)]
        Returns: txid or {tx_hash, merkle}
        """
        height = params[0] if params else 0
        tx_pos = params[1] if len(params) > 1 else 0
        merkle = params[2] if len(params) > 2 else False

        # Query database
        async with self._dal.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT txid FROM transactions
                WHERE block_height = $1 AND index_in_block = $2
                """,
                height,
                tx_pos,
            )

        if not row:
            raise Exception(f"Transaction not found at {height}:{tx_pos}")

        txid = row["txid"]

        if merkle:
            return {"tx_hash": txid, "merkle": []}
        return txid

    # =========================================================================
    # Mempool Methods
    # =========================================================================

    async def mempool_get_fee_histogram(
        self,
        client: "ClientSession",
        params: list[Any],
    ) -> list[list[float | int]]:
        """
        Get mempool fee histogram.

        Returns: [[fee_rate, vsize], ...]
        """
        return self._mempool.get_fee_histogram()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _get_genesis_hash(self) -> str:
        """Get genesis block hash."""
        header = await self._header_store.get_header(0)
        if header:
            return header.hash
        # MIQ mainnet genesis hash
        return "00000000a5e8a7eb02a83fb9693bc2dccbf14ee69d67315c1f151a25cb43fce8"

    async def get_scripthash_status(self, scripthash: str) -> str:
        """Compute current status hash for a scripthash."""
        return await self._dal.get_scripthash_status(scripthash)
