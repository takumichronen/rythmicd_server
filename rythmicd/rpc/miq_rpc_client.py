"""
Miqrod JSON-RPC client.

Provides async communication with the miqrod full node via JSON-RPC over HTTP.
Handles authentication (cookie or user/pass), retries, and error handling.
"""

import asyncio
import base64
from pathlib import Path
from typing import Any

import aiohttp
import orjson

from rythmicd.config.settings import RPCConfig
from rythmicd.logging import get_logger

logger = get_logger(__name__)


class RPCError(Exception):
    """Exception raised for RPC call failures."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"RPC Error {code}: {message}")


class MiqRPCClient:
    """
    Async JSON-RPC client for miqrod.

    Supports cookie-based and user/password authentication.
    Implements automatic retries with exponential backoff.
    """

    def __init__(self, config: RPCConfig):
        """
        Initialize the RPC client.

        Args:
            config: RPC connection configuration
        """
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._request_id = 0
        self._auth_header: str | None = None

    async def start(self) -> None:
        """Start the RPC client and establish connection."""
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        await self._load_auth()
        logger.info(
            "RPC client started",
            host=self.config.host,
            port=self.config.port,
        )

    async def stop(self) -> None:
        """Stop the RPC client and close connection."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("RPC client stopped")

    async def _load_auth(self) -> None:
        """Load authentication credentials from cookie file or config."""
        if self.config.cookie_path:
            cookie_path = Path(self.config.cookie_path)
            if cookie_path.exists():
                cookie = cookie_path.read_text().strip()
                # Cookie format: __cookie__:randomhex
                if ":" in cookie:
                    self._auth_header = self._make_auth_header(
                        cookie.split(":")[0], cookie.split(":")[1]
                    )
                    logger.debug("Loaded auth from cookie file")
                    return

        if self.config.username and self.config.password:
            self._auth_header = self._make_auth_header(
                self.config.username, self.config.password
            )
            logger.debug("Using username/password auth")

    @staticmethod
    def _make_auth_header(username: str, password: str) -> str:
        """Create HTTP Basic Auth header value."""
        credentials = f"{username}:{password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def _next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    async def call(self, method: str, *params: Any) -> Any:
        """
        Make an RPC call to miqrod.

        Args:
            method: RPC method name
            *params: Method parameters

        Returns:
            RPC result value

        Raises:
            RPCError: If the RPC call fails
            aiohttp.ClientError: If network error occurs after retries
        """
        if not self._session:
            raise RuntimeError("RPC client not started")

        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": list(params) if params else [],
        }

        headers = {"Content-Type": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header

        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            try:
                async with self._session.post(
                    self.config.url,
                    data=orjson.dumps(payload),
                    headers=headers,
                ) as response:
                    if response.status == 401:
                        raise RPCError(-32001, "Authentication failed")

                    if response.status != 200:
                        text = await response.text()
                        raise RPCError(
                            -32000,
                            f"HTTP {response.status}: {text[:200]}",
                        )

                    result = await response.json()

                    if "error" in result and result["error"]:
                        error = result["error"]
                        raise RPCError(
                            error.get("code", -32000),
                            error.get("message", "Unknown error"),
                            error.get("data"),
                        )

                    return result.get("result")

            except aiohttp.ClientError as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2**attempt)
                    logger.warning(
                        "RPC call failed, retrying",
                        method=method,
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                continue

        logger.error(
            "RPC call failed after retries",
            method=method,
            attempts=self.config.max_retries,
        )
        if last_error:
            raise last_error
        raise RPCError(-32000, "RPC call failed after retries")

    # =========================================================================
    # Blockchain Query Methods
    # =========================================================================

    async def get_best_block_hash(self) -> str:
        """Get the hash of the best (tip) block."""
        return await self.call("getbestblockhash")

    async def get_block_hash(self, height: int) -> str:
        """Get block hash at a specific height."""
        return await self.call("getblockhash", height)

    async def get_block_count(self) -> int:
        """Get current blockchain height."""
        return await self.call("getblockcount")

    async def get_block(self, hash_or_height: str | int) -> dict[str, Any]:
        """
        Get block data including transactions.

        Args:
            hash_or_height: Block hash (hex string) or height (int)

        Returns:
            Block data with transactions
        """
        return await self.call("getblock", hash_or_height)

    async def get_blockchain_info(self) -> dict[str, Any]:
        """Get blockchain metadata."""
        return await self.call("getblockchaininfo")

    async def get_difficulty(self) -> float:
        """Get current mining difficulty."""
        return await self.call("getdifficulty")

    # =========================================================================
    # Transaction Methods
    # =========================================================================

    async def get_raw_transaction(self, txid: str) -> dict[str, Any]:
        """
        Get transaction details by txid.

        Args:
            txid: Transaction ID (hex string)

        Returns:
            Transaction data including hex, confirmations, etc.
        """
        return await self.call("getrawtransaction", txid)

    async def get_transaction_info(self, txid: str) -> dict[str, Any]:
        """
        Get comprehensive transaction details with input/output breakdown.

        Args:
            txid: Transaction ID (hex string)

        Returns:
            Detailed transaction data
        """
        return await self.call("gettransactioninfo", txid)

    async def send_raw_transaction(self, tx_hex: str) -> dict[str, Any]:
        """
        Broadcast a raw transaction.

        Args:
            tx_hex: Serialized transaction in hex

        Returns:
            Result containing txid or error
        """
        return await self.call("sendrawtransaction", tx_hex)

    async def decode_raw_tx(self, tx_hex: str) -> dict[str, Any]:
        """
        Decode a raw transaction without broadcasting.

        Args:
            tx_hex: Serialized transaction in hex

        Returns:
            Decoded transaction data
        """
        return await self.call("decoderawtx", tx_hex)

    async def get_tx_out(self, txid: str, vout: int) -> dict[str, Any] | None:
        """
        Get unspent transaction output.

        Args:
            txid: Transaction ID
            vout: Output index

        Returns:
            UTXO data or None if spent
        """
        return await self.call("gettxout", txid, vout)

    # =========================================================================
    # Mempool Methods
    # =========================================================================

    async def get_raw_mempool(self) -> list[str]:
        """Get list of all transaction IDs in mempool."""
        return await self.call("getrawmempool")

    async def get_mempool_info(self) -> dict[str, Any]:
        """Get mempool statistics."""
        return await self.call("getmempoolinfo")

    # =========================================================================
    # Address/UTXO Methods
    # =========================================================================

    async def get_address_utxos(self, addresses: str | list[str]) -> list[dict[str, Any]]:
        """
        Get UTXOs for address(es).

        Args:
            addresses: Single address or list of addresses

        Returns:
            List of UTXO data
        """
        return await self.call("getaddressutxos", addresses)

    async def get_address_balance(self, address: str) -> dict[str, Any]:
        """
        Get balance for an address.

        Args:
            address: MIQ address

        Returns:
            Balance data with confirmed/unconfirmed
        """
        return await self.call("getaddressbalance", address)

    async def validate_address(self, address: str) -> dict[str, Any]:
        """
        Validate an address.

        Args:
            address: Address to validate

        Returns:
            Validation result with isvalid flag
        """
        return await self.call("validateaddress", address)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    async def ping(self) -> str:
        """Ping the RPC server."""
        return await self.call("ping")

    async def get_tip_info(self) -> dict[str, Any]:
        """Get current tip metadata."""
        return await self.call("gettipinfo")

    async def uptime(self) -> int:
        """Get server uptime in seconds."""
        return await self.call("uptime")
