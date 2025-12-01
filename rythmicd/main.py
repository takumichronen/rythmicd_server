"""
Rythmicd - Rythmium-compatible server for Miqrochain

Main entry point that orchestrates all components:
- Configuration loading
- Database connection
- RPC client to miqrod
- Block indexer
- Mempool tracker
- Rythmium protocol server
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

from rythmicd import __version__
from rythmicd.config.settings import Settings, load_settings
from rythmicd.db.dal import DatabaseManager, DAL
from rythmicd.headers.header_store import HeaderStore
from rythmicd.indexer.block_indexer import BlockIndexer
from rythmicd.indexer.mempool_tracker import MempoolTracker
from rythmicd.logging import setup_logging, get_logger
from rythmicd.protocol.handlers import RythmiumHandlers
from rythmicd.protocol.server import RythmiumServer
from rythmicd.rpc.miq_rpc_client import MiqRPCClient


class Rythmicd:
    """
    Main application class.

    Manages the lifecycle of all components and coordinates
    their interactions.
    """

    def __init__(self, settings: Settings):
        """
        Initialize rythmicd.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self.logger = get_logger("rythmicd")

        # Components (initialized in start())
        self._rpc: Optional[MiqRPCClient] = None
        self._db_manager: Optional[DatabaseManager] = None
        self._dal: Optional[DAL] = None
        self._header_store: Optional[HeaderStore] = None
        self._indexer: Optional[BlockIndexer] = None
        self._mempool: Optional[MempoolTracker] = None
        self._handlers: Optional[RythmiumHandlers] = None
        self._server: Optional[RythmiumServer] = None

        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start all components."""
        self.logger.info(
            "Starting rythmicd",
            version=__version__,
            network=self.settings.network,
        )

        self._running = True

        try:
            # Initialize RPC client
            self._rpc = MiqRPCClient(self.settings.rpc)
            await self._rpc.start()

            # Test RPC connection
            try:
                info = await self._rpc.get_blockchain_info()
                self.logger.info(
                    "Connected to miqrod",
                    chain=info.get("chain"),
                    height=info.get("height"),
                )
            except Exception as e:
                self.logger.error("Failed to connect to miqrod", error=str(e))
                raise

            # Initialize database
            self._db_manager = DatabaseManager(self.settings.database)
            await self._db_manager.start()
            self._dal = DAL(self._db_manager)

            # Initialize header store
            self._header_store = HeaderStore(self._dal)
            await self._header_store.load_cache()

            # Initialize mempool tracker
            self._mempool = MempoolTracker(
                self._rpc,
                self._dal,
                self.settings.indexer,
            )

            # Initialize block indexer
            self._indexer = BlockIndexer(
                self._rpc,
                self._dal,
                self._header_store,
                self.settings.indexer,
            )

            # Initialize protocol handlers
            self._handlers = RythmiumHandlers(
                self._rpc,
                self._dal,
                self._header_store,
                self._mempool,
                self.settings.rythmium,
            )

            # Initialize protocol server
            self._server = RythmiumServer(self._handlers, self.settings.rythmium)

            # Set up notification callbacks
            self._indexer.set_notify_callback(self._on_new_block)
            self._mempool.set_notify_callback(self._on_mempool_change)

            # Start components
            await self._mempool.start()
            await self._indexer.start()
            await self._server.start()

            self.logger.info("Rythmicd started successfully")

        except Exception as e:
            self.logger.error("Failed to start rythmicd", error=str(e))
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop all components."""
        self.logger.info("Stopping rythmicd")
        self._running = False

        # Stop components in reverse order
        if self._server:
            await self._server.stop()

        if self._indexer:
            await self._indexer.stop()

        if self._mempool:
            await self._mempool.stop()

        if self._rpc:
            await self._rpc.stop()

        if self._db_manager:
            await self._db_manager.stop()

        self.logger.info("Rythmicd stopped")
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    async def _on_new_block(
        self,
        height: int,
        block_hash: str,
        affected_scripthashes: set[str],
    ) -> None:
        """Handle new block notification."""
        self.logger.debug(
            "New block indexed",
            height=height,
            hash=block_hash[:16],
            affected=len(affected_scripthashes),
        )

        if self._server and self._header_store:
            # Notify header subscribers
            header = await self._header_store.get_header(height)
            if header:
                await self._server.notify_new_block(
                    height,
                    block_hash,
                    header.header_bytes.hex(),
                )

            # Notify scripthash subscribers
            subscribed = self._server.get_subscribed_scripthashes()
            affected_subscribed = affected_scripthashes & subscribed
            if affected_subscribed:
                await self._server.notify_scripthashes(affected_subscribed)

    async def _on_mempool_change(self, affected_scripthashes: set[str]) -> None:
        """Handle mempool change notification."""
        if self._server:
            subscribed = self._server.get_subscribed_scripthashes()
            affected_subscribed = affected_scripthashes & subscribed
            if affected_subscribed:
                await self._server.notify_scripthashes(affected_subscribed)

    @property
    def status(self) -> dict:
        """Get current status."""
        return {
            "running": self._running,
            "indexed_height": self._indexer.indexed_height if self._indexer else -1,
            "node_height": self._indexer.node_height if self._indexer else -1,
            "is_synced": self._indexer.is_synced if self._indexer else False,
            "connected_clients": self._server.client_count if self._server else 0,
            "mempool_size": len(self._mempool.state.transactions) if self._mempool else 0,
        }


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Rythmicd - Rythmium-compatible server for Miqrochain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"rythmicd {__version__}",
    )

    parser.add_argument(
        "-c", "--config",
        type=str,
        help="Path to YAML configuration file",
    )

    parser.add_argument(
        "--env-file",
        type=str,
        help="Path to .env file",
    )

    # RPC options
    parser.add_argument(
        "--rpc-host",
        type=str,
        help="Miqrod RPC host",
    )

    parser.add_argument(
        "--rpc-port",
        type=int,
        help="Miqrod RPC port",
    )

    parser.add_argument(
        "--rpc-user",
        type=str,
        help="Miqrod RPC username",
    )

    parser.add_argument(
        "--rpc-password",
        type=str,
        help="Miqrod RPC password",
    )

    parser.add_argument(
        "--rpc-cookie",
        type=str,
        help="Path to miqrod RPC cookie file",
    )

    # Database options
    parser.add_argument(
        "--db-host",
        type=str,
        help="PostgreSQL host",
    )

    parser.add_argument(
        "--db-port",
        type=int,
        help="PostgreSQL port",
    )

    parser.add_argument(
        "--db-name",
        type=str,
        help="PostgreSQL database name",
    )

    parser.add_argument(
        "--db-user",
        type=str,
        help="PostgreSQL username",
    )

    parser.add_argument(
        "--db-password",
        type=str,
        help="PostgreSQL password",
    )

    # Rythmium server options
    parser.add_argument(
        "--rythmium-host",
        type=str,
        help="Rythmium server bind address",
    )

    parser.add_argument(
        "--rythmium-port",
        type=int,
        help="Rythmium server TCP port",
    )

    # Logging options
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    parser.add_argument(
        "--log-json",
        action="store_true",
        help="Output logs in JSON format",
    )

    return parser.parse_args()


def apply_cli_args(settings: Settings, args: argparse.Namespace) -> Settings:
    """Apply CLI arguments to settings (CLI takes precedence)."""
    # RPC settings
    if args.rpc_host:
        settings.rpc.host = args.rpc_host
    if args.rpc_port:
        settings.rpc.port = args.rpc_port
    if args.rpc_user:
        settings.rpc.username = args.rpc_user
    if args.rpc_password:
        settings.rpc.password = args.rpc_password
    if args.rpc_cookie:
        settings.rpc.cookie_path = args.rpc_cookie

    # Database settings
    if args.db_host:
        settings.database.host = args.db_host
    if args.db_port:
        settings.database.port = args.db_port
    if args.db_name:
        settings.database.database = args.db_name
    if args.db_user:
        settings.database.username = args.db_user
    if args.db_password:
        settings.database.password = args.db_password

    # Rythmium settings
    if args.rythmium_host:
        settings.rythmium.host = args.rythmium_host
    if args.rythmium_port:
        settings.rythmium.port = args.rythmium_port

    # Logging settings
    if args.log_level:
        settings.logging.level = args.log_level
    if args.log_json:
        settings.logging.json_format = True

    return settings


async def run_server(settings: Settings) -> None:
    """Run the server with signal handling."""
    server = Rythmicd(settings)

    # Set up signal handlers
    loop = asyncio.get_event_loop()

    def handle_signal() -> None:
        logger = get_logger("main")
        logger.info("Received shutdown signal")
        asyncio.create_task(server.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    try:
        await server.start()
        await server.wait_for_shutdown()
    except Exception as e:
        logger = get_logger("main")
        logger.error("Server error", error=str(e))
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Load settings
    settings = load_settings(
        config_path=args.config,
        env_file=args.env_file,
    )

    # Apply CLI arguments
    settings = apply_cli_args(settings, args)

    # Set up logging
    setup_logging(
        level=settings.logging.level,
        json_format=settings.logging.json_format,
        module_levels=settings.logging.module_levels,
    )

    logger = get_logger("main")
    logger.info("Rythmicd starting", version=__version__)

    # Use uvloop if available (better performance)
    try:
        import uvloop
        uvloop.install()
        logger.info("Using uvloop")
    except ImportError:
        pass

    # Run the server
    asyncio.run(run_server(settings))


if __name__ == "__main__":
    main()
