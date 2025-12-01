"""
Rythmium protocol TCP server.

Implements JSON-RPC 2.0 over TCP for Rythmium wallet compatibility.
Handles client connections, subscriptions, and notifications.
"""

import asyncio
import ssl
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

import orjson

from rythmicd.config.settings import RythmiumServerConfig
from rythmicd.logging import get_logger
from rythmicd.protocol.handlers import RythmiumHandlers

logger = get_logger(__name__)


@dataclass
class ClientSession:
    """Represents a connected client session."""

    id: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    address: str
    port: int

    # Subscription state
    subscribed_headers: bool = False
    subscribed_scripthashes: set[str] = field(default_factory=set)

    # Protocol negotiation
    protocol_version: str = "1.4"
    client_name: str = ""

    # Rate limiting
    request_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "id": self.id[:8],
            "address": self.address,
            "subscribed_headers": self.subscribed_headers,
            "subscribed_scripthashes": len(self.subscribed_scripthashes),
        }


class RythmiumServer:
    """
    Rythmium protocol server.

    Handles TCP connections from Rythmium-compatible wallets,
    processes JSON-RPC requests, and sends notifications.
    """

    def __init__(
        self,
        handlers: RythmiumHandlers,
        config: RythmiumServerConfig,
    ):
        """
        Initialize Rythmium server.

        Args:
            handlers: Request handlers
            config: Server configuration
        """
        self._handlers = handlers
        self._config = config

        self._server: Optional[asyncio.Server] = None
        self._ssl_server: Optional[asyncio.Server] = None
        self._clients: dict[str, ClientSession] = {}
        self._running = False

        # Set up handlers callback for server reference
        self._handlers.set_server(self)

    @property
    def client_count(self) -> int:
        """Get number of connected clients."""
        return len(self._clients)

    async def start(self) -> None:
        """Start the server."""
        self._running = True

        # Start TCP server
        self._server = await asyncio.start_server(
            self._handle_client,
            self._config.host,
            self._config.port,
            limit=self._config.max_message_size,
        )

        logger.info(
            "Rythmium TCP server started",
            host=self._config.host,
            port=self._config.port,
        )

        # Start SSL server if configured
        if self._config.ssl_port and self._config.ssl_cert_path and self._config.ssl_key_path:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(
                self._config.ssl_cert_path,
                self._config.ssl_key_path,
            )

            self._ssl_server = await asyncio.start_server(
                self._handle_client,
                self._config.host,
                self._config.ssl_port,
                ssl=ssl_context,
                limit=self._config.max_message_size,
            )

            logger.info(
                "Rythmium SSL server started",
                host=self._config.host,
                port=self._config.ssl_port,
            )

    async def stop(self) -> None:
        """Stop the server."""
        self._running = False

        # Close all client connections
        for client in list(self._clients.values()):
            await self._close_client(client)

        # Stop servers
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        if self._ssl_server:
            self._ssl_server.close()
            await self._ssl_server.wait_closed()

        logger.info("Rythmium server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new client connection."""
        # Get client address
        addr = writer.get_extra_info("peername")
        address = addr[0] if addr else "unknown"
        port = addr[1] if addr else 0

        # Check connection limit
        if len(self._clients) >= self._config.max_connections:
            logger.warning(
                "Connection rejected: max connections reached",
                address=address,
            )
            writer.close()
            await writer.wait_closed()
            return

        # Create session
        client = ClientSession(
            id=str(uuid.uuid4()),
            reader=reader,
            writer=writer,
            address=address,
            port=port,
        )

        self._clients[client.id] = client
        logger.info("Client connected", **client.to_dict())

        try:
            await self._client_loop(client)
        except Exception as e:
            logger.error("Client error", client_id=client.id[:8], error=str(e))
        finally:
            await self._close_client(client)

    async def _client_loop(self, client: ClientSession) -> None:
        """Main loop for a client connection."""
        buffer = b""

        while self._running:
            try:
                # Read data with timeout
                data = await asyncio.wait_for(
                    client.reader.read(8192),
                    timeout=self._config.client_timeout,
                )

                if not data:
                    break  # Client disconnected

                buffer += data

                # Process complete messages (newline-delimited JSON)
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line:
                        await self._process_message(client, line)

            except asyncio.TimeoutError:
                logger.debug("Client timeout", client_id=client.id[:8])
                break
            except ConnectionResetError:
                break
            except Exception as e:
                logger.error(
                    "Client read error",
                    client_id=client.id[:8],
                    error=str(e),
                )
                break

    async def _process_message(
        self,
        client: ClientSession,
        data: bytes,
    ) -> None:
        """Process a single JSON-RPC message."""
        # Check message size
        if len(data) > self._config.max_message_size:
            await self._send_error(client, None, -32600, "Message too large")
            return

        try:
            request = orjson.loads(data)
        except Exception:
            await self._send_error(client, None, -32700, "Parse error")
            return

        # Handle batch requests
        if isinstance(request, list):
            responses = []
            for req in request:
                response = await self._handle_request(client, req)
                if response:
                    responses.append(response)
            if responses:
                await self._send_response(client, responses)
        else:
            response = await self._handle_request(client, request)
            if response:
                await self._send_response(client, response)

    async def _handle_request(
        self,
        client: ClientSession,
        request: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Handle a single JSON-RPC request."""
        # Validate request structure
        if not isinstance(request, dict):
            return self._make_error(None, -32600, "Invalid request")

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", [])

        if not method or not isinstance(method, str):
            return self._make_error(request_id, -32600, "Invalid request")

        # Increment request count for rate limiting
        client.request_count += 1

        logger.debug(
            "RPC request",
            client_id=client.id[:8],
            method=method,
        )

        try:
            result = await self._handlers.handle(client, method, params)
            return self._make_result(request_id, result)
        except Exception as e:
            logger.error(
                "Handler error",
                method=method,
                error=str(e),
            )
            return self._make_error(request_id, -32000, str(e))

    async def _send_response(
        self,
        client: ClientSession,
        response: dict[str, Any] | list[dict[str, Any]],
    ) -> None:
        """Send a response to a client."""
        try:
            data = orjson.dumps(response) + b"\n"
            client.writer.write(data)
            await client.writer.drain()
        except Exception as e:
            logger.error(
                "Send error",
                client_id=client.id[:8],
                error=str(e),
            )

    async def _send_error(
        self,
        client: ClientSession,
        request_id: Any,
        code: int,
        message: str,
    ) -> None:
        """Send an error response."""
        await self._send_response(client, self._make_error(request_id, code, message))

    @staticmethod
    def _make_result(request_id: Any, result: Any) -> dict[str, Any]:
        """Create a JSON-RPC result response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    @staticmethod
    def _make_error(
        request_id: Any,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        """Create a JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    async def _close_client(self, client: ClientSession) -> None:
        """Close a client connection."""
        self._clients.pop(client.id, None)

        try:
            client.writer.close()
            await client.writer.wait_closed()
        except Exception:
            pass

        logger.info("Client disconnected", **client.to_dict())

    # =========================================================================
    # Notification Methods
    # =========================================================================

    async def notify_new_block(
        self,
        height: int,
        block_hash: str,
        header_hex: str,
    ) -> None:
        """Notify subscribed clients of a new block."""
        notification = {
            "jsonrpc": "2.0",
            "method": "blockchain.headers.subscribe",
            "params": [{"height": height, "hex": header_hex}],
        }

        for client in self._clients.values():
            if client.subscribed_headers:
                try:
                    await self._send_response(client, notification)
                except Exception:
                    pass

    async def notify_scripthash(
        self,
        scripthash: str,
        status: str,
    ) -> None:
        """Notify subscribed clients of a scripthash status change."""
        notification = {
            "jsonrpc": "2.0",
            "method": "blockchain.scripthash.subscribe",
            "params": [scripthash, status],
        }

        for client in self._clients.values():
            if scripthash in client.subscribed_scripthashes:
                try:
                    await self._send_response(client, notification)
                except Exception:
                    pass

    async def notify_scripthashes(self, scripthashes: set[str]) -> None:
        """Notify subscribed clients of multiple scripthash changes."""
        for scripthash in scripthashes:
            # Get new status
            status = await self._handlers.get_scripthash_status(scripthash)
            await self.notify_scripthash(scripthash, status)

    def get_subscribed_scripthashes(self) -> set[str]:
        """Get all scripthashes with active subscriptions."""
        result: set[str] = set()
        for client in self._clients.values():
            result.update(client.subscribed_scripthashes)
        return result
