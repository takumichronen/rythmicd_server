"""
Data Access Layer for PostgreSQL.

Provides async database operations for blocks, transactions, UTXOs,
and scripthash history. Uses asyncpg for high-performance async I/O.
"""

import hashlib
from typing import Any, Optional

import asyncpg

from rythmicd.config.settings import DatabaseConfig
from rythmicd.db.models import Block, Header, Transaction, UTXO, ScripthashHistory
from rythmicd.logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """
    Manages PostgreSQL connection pool and schema migrations.
    """

    def __init__(self, config: DatabaseConfig):
        """
        Initialize database manager.

        Args:
            config: Database configuration
        """
        self.config = config
        self._pool: Optional[asyncpg.Pool] = None

    async def start(self) -> None:
        """Start the database connection pool."""
        logger.info(
            "Connecting to database",
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
        )

        self._pool = await asyncpg.create_pool(
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            user=self.config.username,
            password=self.config.password,
            min_size=self.config.min_connections,
            max_size=self.config.max_connections,
            command_timeout=self.config.command_timeout,
        )

        await self._run_migrations()
        logger.info("Database connection pool established")

    async def stop(self) -> None:
        """Stop the database connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("Database connection pool closed")

    @property
    def pool(self) -> asyncpg.Pool:
        """Get the connection pool."""
        if not self._pool:
            raise RuntimeError("Database not started")
        return self._pool

    async def _run_migrations(self) -> None:
        """Run database schema migrations."""
        async with self._pool.acquire() as conn:
            # Create tables if they don't exist
            await conn.execute(SCHEMA_SQL)
            logger.info("Database schema verified/created")


# SQL schema for all tables
SCHEMA_SQL = """
-- Blocks table
CREATE TABLE IF NOT EXISTS blocks (
    height BIGINT PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    prev_hash TEXT NOT NULL,
    merkle_root TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    version INTEGER NOT NULL,
    bits BIGINT NOT NULL,
    nonce BIGINT NOT NULL,
    tx_count INTEGER NOT NULL,
    size INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash);

-- Headers table (for SPV)
CREATE TABLE IF NOT EXISTS headers (
    height BIGINT PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    prev_hash TEXT NOT NULL,
    header_bytes BYTEA NOT NULL
);

-- Transactions table
CREATE TABLE IF NOT EXISTS transactions (
    txid TEXT PRIMARY KEY,
    block_hash TEXT NOT NULL,
    block_height BIGINT NOT NULL,
    index_in_block INTEGER NOT NULL,
    raw_tx TEXT NOT NULL,
    fee BIGINT NOT NULL DEFAULT 0,
    size INTEGER NOT NULL,
    is_coinbase BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    locktime BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tx_block_height ON transactions(block_height);
CREATE INDEX IF NOT EXISTS idx_tx_block_hash ON transactions(block_hash);

-- UTXOs table
CREATE TABLE IF NOT EXISTS utxos (
    txid TEXT NOT NULL,
    vout INTEGER NOT NULL,
    scripthash TEXT NOT NULL,
    pkh TEXT NOT NULL,
    value BIGINT NOT NULL,
    height BIGINT NOT NULL,
    is_coinbase BOOLEAN NOT NULL DEFAULT FALSE,
    is_spent BOOLEAN NOT NULL DEFAULT FALSE,
    spent_by_txid TEXT,
    spent_height BIGINT,
    PRIMARY KEY (txid, vout)
);

CREATE INDEX IF NOT EXISTS idx_utxos_scripthash ON utxos(scripthash);
CREATE INDEX IF NOT EXISTS idx_utxos_scripthash_unspent ON utxos(scripthash) WHERE NOT is_spent;
CREATE INDEX IF NOT EXISTS idx_utxos_height ON utxos(height);

-- Scripthash history table
CREATE TABLE IF NOT EXISTS scripthash_history (
    scripthash TEXT NOT NULL,
    txid TEXT NOT NULL,
    height BIGINT NOT NULL,
    tx_pos INTEGER NOT NULL,
    value_delta BIGINT NOT NULL,
    PRIMARY KEY (scripthash, txid, tx_pos)
);

CREATE INDEX IF NOT EXISTS idx_sh_history_scripthash ON scripthash_history(scripthash);
CREATE INDEX IF NOT EXISTS idx_sh_history_height ON scripthash_history(height);

-- Scripthash status cache (optional, for fast subscription updates)
CREATE TABLE IF NOT EXISTS scripthash_status (
    scripthash TEXT PRIMARY KEY,
    status_hash TEXT NOT NULL,
    confirmed_balance BIGINT NOT NULL DEFAULT 0,
    unconfirmed_balance BIGINT NOT NULL DEFAULT 0,
    last_updated BIGINT NOT NULL
);

-- Metadata table for tracking indexer state
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DAL:
    """
    Data Access Layer providing async database operations.
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize DAL.

        Args:
            db_manager: Database manager instance
        """
        self._db = db_manager

    @property
    def pool(self) -> asyncpg.Pool:
        """Get the connection pool."""
        return self._db.pool

    # =========================================================================
    # Metadata Operations
    # =========================================================================

    async def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value by key."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM metadata WHERE key = $1", key
            )
            return row["value"] if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO metadata (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = $2
                """,
                key,
                value,
            )

    async def get_indexed_height(self) -> int:
        """Get the current indexed blockchain height."""
        value = await self.get_metadata("indexed_height")
        return int(value) if value else -1

    async def set_indexed_height(self, height: int) -> None:
        """Set the current indexed blockchain height."""
        await self.set_metadata("indexed_height", str(height))

    # =========================================================================
    # Block Operations
    # =========================================================================

    async def insert_block(self, block: Block) -> None:
        """Insert a block record."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO blocks (
                    height, hash, prev_hash, merkle_root, timestamp,
                    version, bits, nonce, tx_count, size
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (height) DO UPDATE SET
                    hash = $2, prev_hash = $3, merkle_root = $4, timestamp = $5,
                    version = $6, bits = $7, nonce = $8, tx_count = $9, size = $10
                """,
                block.height,
                block.hash,
                block.prev_hash,
                block.merkle_root,
                block.timestamp,
                block.version,
                block.bits,
                block.nonce,
                block.tx_count,
                block.size,
            )

    async def get_block_by_height(self, height: int) -> Optional[Block]:
        """Get block by height."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM blocks WHERE height = $1", height
            )
            if not row:
                return None
            return Block(**dict(row))

    async def get_block_by_hash(self, block_hash: str) -> Optional[Block]:
        """Get block by hash."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM blocks WHERE hash = $1", block_hash
            )
            if not row:
                return None
            return Block(**dict(row))

    async def get_tip_block(self) -> Optional[Block]:
        """Get the highest block."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM blocks ORDER BY height DESC LIMIT 1"
            )
            if not row:
                return None
            return Block(**dict(row))

    async def delete_blocks_above(self, height: int) -> int:
        """Delete all blocks above a given height. Returns count deleted."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM blocks WHERE height > $1", height
            )
            count = int(result.split()[-1])
            return count

    # =========================================================================
    # Header Operations
    # =========================================================================

    async def insert_header(self, header: Header) -> None:
        """Insert a header record."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO headers (height, hash, prev_hash, header_bytes)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (height) DO UPDATE SET
                    hash = $2, prev_hash = $3, header_bytes = $4
                """,
                header.height,
                header.hash,
                header.prev_hash,
                header.header_bytes,
            )

    async def get_header_by_height(self, height: int) -> Optional[Header]:
        """Get header by height."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM headers WHERE height = $1", height
            )
            if not row:
                return None
            return Header(**dict(row))

    async def get_headers_range(
        self, start_height: int, count: int
    ) -> list[Header]:
        """Get a range of headers."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM headers
                WHERE height >= $1 AND height < $2
                ORDER BY height
                """,
                start_height,
                start_height + count,
            )
            return [Header(**dict(row)) for row in rows]

    async def delete_headers_above(self, height: int) -> int:
        """Delete all headers above a given height."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM headers WHERE height > $1", height
            )
            count = int(result.split()[-1])
            return count

    # =========================================================================
    # Transaction Operations
    # =========================================================================

    async def insert_transaction(self, tx: Transaction) -> None:
        """Insert a transaction record."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO transactions (
                    txid, block_hash, block_height, index_in_block,
                    raw_tx, fee, size, is_coinbase, version, locktime
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (txid) DO UPDATE SET
                    block_hash = $2, block_height = $3, index_in_block = $4,
                    raw_tx = $5, fee = $6, size = $7, is_coinbase = $8,
                    version = $9, locktime = $10
                """,
                tx.txid,
                tx.block_hash,
                tx.block_height,
                tx.index_in_block,
                tx.raw_tx,
                tx.fee,
                tx.size,
                tx.is_coinbase,
                tx.version,
                tx.locktime,
            )

    async def get_transaction(self, txid: str) -> Optional[Transaction]:
        """Get transaction by txid."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM transactions WHERE txid = $1", txid
            )
            if not row:
                return None
            return Transaction(**dict(row), inputs=[], outputs=[])

    async def delete_transactions_at_height(self, height: int) -> list[str]:
        """Delete transactions at a given height. Returns list of deleted txids."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT txid FROM transactions WHERE block_height = $1", height
            )
            txids = [row["txid"] for row in rows]
            await conn.execute(
                "DELETE FROM transactions WHERE block_height = $1", height
            )
            return txids

    # =========================================================================
    # UTXO Operations
    # =========================================================================

    async def insert_utxo(self, utxo: UTXO) -> None:
        """Insert a UTXO record."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO utxos (
                    txid, vout, scripthash, pkh, value, height,
                    is_coinbase, is_spent, spent_by_txid, spent_height
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (txid, vout) DO UPDATE SET
                    scripthash = $3, pkh = $4, value = $5, height = $6,
                    is_coinbase = $7, is_spent = $8, spent_by_txid = $9, spent_height = $10
                """,
                utxo.txid,
                utxo.vout,
                utxo.scripthash,
                utxo.pkh,
                utxo.value,
                utxo.height,
                utxo.is_coinbase,
                utxo.is_spent,
                utxo.spent_by_txid,
                utxo.spent_height,
            )

    async def insert_utxos_batch(self, utxos: list[UTXO]) -> None:
        """Insert multiple UTXOs in a batch."""
        if not utxos:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO utxos (
                    txid, vout, scripthash, pkh, value, height,
                    is_coinbase, is_spent, spent_by_txid, spent_height
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (txid, vout) DO UPDATE SET
                    is_spent = $8, spent_by_txid = $9, spent_height = $10
                """,
                [
                    (
                        u.txid,
                        u.vout,
                        u.scripthash,
                        u.pkh,
                        u.value,
                        u.height,
                        u.is_coinbase,
                        u.is_spent,
                        u.spent_by_txid,
                        u.spent_height,
                    )
                    for u in utxos
                ],
            )

    async def spend_utxo(
        self, txid: str, vout: int, spent_by: str, spent_height: int
    ) -> Optional[UTXO]:
        """Mark a UTXO as spent. Returns the UTXO if found."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE utxos
                SET is_spent = TRUE, spent_by_txid = $3, spent_height = $4
                WHERE txid = $1 AND vout = $2
                RETURNING *
                """,
                txid,
                vout,
                spent_by,
                spent_height,
            )
            if not row:
                return None
            return UTXO(**dict(row))

    async def unspend_utxo(self, txid: str, vout: int) -> None:
        """Mark a UTXO as unspent (for reorg rollback)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE utxos
                SET is_spent = FALSE, spent_by_txid = NULL, spent_height = NULL
                WHERE txid = $1 AND vout = $2
                """,
                txid,
                vout,
            )

    async def get_utxo(self, txid: str, vout: int) -> Optional[UTXO]:
        """Get UTXO by outpoint."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM utxos WHERE txid = $1 AND vout = $2",
                txid,
                vout,
            )
            if not row:
                return None
            return UTXO(**dict(row))

    async def get_utxos_for_scripthash(
        self, scripthash: str, include_spent: bool = False
    ) -> list[UTXO]:
        """Get all UTXOs for a scripthash."""
        async with self.pool.acquire() as conn:
            if include_spent:
                rows = await conn.fetch(
                    "SELECT * FROM utxos WHERE scripthash = $1 ORDER BY height",
                    scripthash,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM utxos
                    WHERE scripthash = $1 AND NOT is_spent
                    ORDER BY height
                    """,
                    scripthash,
                )
            return [UTXO(**dict(row)) for row in rows]

    async def get_balance_for_scripthash(self, scripthash: str) -> dict[str, int]:
        """Get confirmed and unconfirmed balance for a scripthash."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN NOT is_spent THEN value ELSE 0 END), 0) as confirmed
                FROM utxos
                WHERE scripthash = $1
                """,
                scripthash,
            )
            return {
                "confirmed": row["confirmed"] if row else 0,
                "unconfirmed": 0,  # Mempool balance added separately
            }

    async def delete_utxos_at_height(self, height: int) -> int:
        """Delete UTXOs created at a given height."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM utxos WHERE height = $1", height
            )
            count = int(result.split()[-1])
            return count

    async def unspend_utxos_at_height(self, height: int) -> int:
        """Unspend UTXOs that were spent at a given height."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE utxos
                SET is_spent = FALSE, spent_by_txid = NULL, spent_height = NULL
                WHERE spent_height = $1
                """,
                height,
            )
            count = int(result.split()[-1])
            return count

    # =========================================================================
    # Scripthash History Operations
    # =========================================================================

    async def insert_scripthash_history(self, entry: ScripthashHistory) -> None:
        """Insert a scripthash history entry."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO scripthash_history (
                    scripthash, txid, height, tx_pos, value_delta
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (scripthash, txid, tx_pos) DO NOTHING
                """,
                entry.scripthash,
                entry.txid,
                entry.height,
                entry.tx_pos,
                entry.value_delta,
            )

    async def insert_scripthash_history_batch(
        self, entries: list[ScripthashHistory]
    ) -> None:
        """Insert multiple scripthash history entries."""
        if not entries:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO scripthash_history (
                    scripthash, txid, height, tx_pos, value_delta
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (scripthash, txid, tx_pos) DO NOTHING
                """,
                [
                    (e.scripthash, e.txid, e.height, e.tx_pos, e.value_delta)
                    for e in entries
                ],
            )

    async def get_scripthash_history(
        self, scripthash: str
    ) -> list[ScripthashHistory]:
        """Get transaction history for a scripthash."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM scripthash_history
                WHERE scripthash = $1
                ORDER BY height, tx_pos
                """,
                scripthash,
            )
            return [ScripthashHistory(**dict(row)) for row in rows]

    async def delete_scripthash_history_at_height(self, height: int) -> int:
        """Delete scripthash history at a given height."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM scripthash_history WHERE height = $1", height
            )
            count = int(result.split()[-1])
            return count

    async def get_scripthash_status(self, scripthash: str) -> str:
        """
        Compute the status hash for a scripthash.

        The status is SHA256 of the concatenated tx_hash:height pairs.
        """
        history = await self.get_scripthash_history(scripthash)
        if not history:
            return ""

        # Build status string: concatenate txid:height for each entry
        status_parts = []
        seen_txids: set[str] = set()
        for entry in history:
            if entry.txid not in seen_txids:
                status_parts.append(f"{entry.txid}:{entry.height}:")
                seen_txids.add(entry.txid)

        if not status_parts:
            return ""

        status_str = "".join(status_parts)
        return hashlib.sha256(status_str.encode()).hexdigest()

    # =========================================================================
    # Batch Operations for Indexing
    # =========================================================================

    async def index_block_batch(
        self,
        block: Block,
        header: Header,
        transactions: list[Transaction],
        utxos_created: list[UTXO],
        utxos_spent: list[tuple[str, int, str, int]],  # (txid, vout, spent_by, height)
        history_entries: list[ScripthashHistory],
    ) -> None:
        """
        Index a complete block in a single transaction.

        Args:
            block: Block record
            header: Header record
            transactions: List of transactions
            utxos_created: List of new UTXOs
            utxos_spent: List of (txid, vout, spent_by_txid, spent_height) tuples
            history_entries: List of scripthash history entries
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Insert block
                await conn.execute(
                    """
                    INSERT INTO blocks (
                        height, hash, prev_hash, merkle_root, timestamp,
                        version, bits, nonce, tx_count, size
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (height) DO UPDATE SET
                        hash = $2, prev_hash = $3, merkle_root = $4, timestamp = $5,
                        version = $6, bits = $7, nonce = $8, tx_count = $9, size = $10
                    """,
                    block.height,
                    block.hash,
                    block.prev_hash,
                    block.merkle_root,
                    block.timestamp,
                    block.version,
                    block.bits,
                    block.nonce,
                    block.tx_count,
                    block.size,
                )

                # Insert header
                await conn.execute(
                    """
                    INSERT INTO headers (height, hash, prev_hash, header_bytes)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (height) DO UPDATE SET
                        hash = $2, prev_hash = $3, header_bytes = $4
                    """,
                    header.height,
                    header.hash,
                    header.prev_hash,
                    header.header_bytes,
                )

                # Insert transactions
                if transactions:
                    await conn.executemany(
                        """
                        INSERT INTO transactions (
                            txid, block_hash, block_height, index_in_block,
                            raw_tx, fee, size, is_coinbase, version, locktime
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (txid) DO UPDATE SET
                            block_hash = $2, block_height = $3, index_in_block = $4,
                            raw_tx = $5, fee = $6, size = $7, is_coinbase = $8,
                            version = $9, locktime = $10
                        """,
                        [
                            (
                                tx.txid,
                                tx.block_hash,
                                tx.block_height,
                                tx.index_in_block,
                                tx.raw_tx,
                                tx.fee,
                                tx.size,
                                tx.is_coinbase,
                                tx.version,
                                tx.locktime,
                            )
                            for tx in transactions
                        ],
                    )

                # Insert new UTXOs
                if utxos_created:
                    await conn.executemany(
                        """
                        INSERT INTO utxos (
                            txid, vout, scripthash, pkh, value, height,
                            is_coinbase, is_spent, spent_by_txid, spent_height
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (txid, vout) DO NOTHING
                        """,
                        [
                            (
                                u.txid,
                                u.vout,
                                u.scripthash,
                                u.pkh,
                                u.value,
                                u.height,
                                u.is_coinbase,
                                u.is_spent,
                                u.spent_by_txid,
                                u.spent_height,
                            )
                            for u in utxos_created
                        ],
                    )

                # Mark UTXOs as spent
                if utxos_spent:
                    await conn.executemany(
                        """
                        UPDATE utxos
                        SET is_spent = TRUE, spent_by_txid = $3, spent_height = $4
                        WHERE txid = $1 AND vout = $2
                        """,
                        utxos_spent,
                    )

                # Insert history entries
                if history_entries:
                    await conn.executemany(
                        """
                        INSERT INTO scripthash_history (
                            scripthash, txid, height, tx_pos, value_delta
                        ) VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (scripthash, txid, tx_pos) DO NOTHING
                        """,
                        [
                            (e.scripthash, e.txid, e.height, e.tx_pos, e.value_delta)
                            for e in history_entries
                        ],
                    )

                # Update indexed height
                await conn.execute(
                    """
                    INSERT INTO metadata (key, value) VALUES ('indexed_height', $1)
                    ON CONFLICT (key) DO UPDATE SET value = $1
                    """,
                    str(block.height),
                )

    async def rollback_block(self, height: int) -> set[str]:
        """
        Roll back a block during reorg.

        Returns set of affected scripthashes.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Get affected scripthashes before deletion
                rows = await conn.fetch(
                    "SELECT DISTINCT scripthash FROM scripthash_history WHERE height = $1",
                    height,
                )
                affected = {row["scripthash"] for row in rows}

                # Also get scripthashes from UTXOs created at this height
                rows = await conn.fetch(
                    "SELECT DISTINCT scripthash FROM utxos WHERE height = $1",
                    height,
                )
                affected.update(row["scripthash"] for row in rows)

                # Unspend UTXOs that were spent at this height
                await conn.execute(
                    """
                    UPDATE utxos
                    SET is_spent = FALSE, spent_by_txid = NULL, spent_height = NULL
                    WHERE spent_height = $1
                    """,
                    height,
                )

                # Delete UTXOs created at this height
                await conn.execute(
                    "DELETE FROM utxos WHERE height = $1", height
                )

                # Delete scripthash history at this height
                await conn.execute(
                    "DELETE FROM scripthash_history WHERE height = $1", height
                )

                # Delete transactions at this height
                await conn.execute(
                    "DELETE FROM transactions WHERE block_height = $1", height
                )

                # Delete header at this height
                await conn.execute(
                    "DELETE FROM headers WHERE height = $1", height
                )

                # Delete block at this height
                await conn.execute(
                    "DELETE FROM blocks WHERE height = $1", height
                )

                # Update indexed height
                await conn.execute(
                    """
                    INSERT INTO metadata (key, value) VALUES ('indexed_height', $1)
                    ON CONFLICT (key) DO UPDATE SET value = $1
                    """,
                    str(height - 1),
                )

                return affected
