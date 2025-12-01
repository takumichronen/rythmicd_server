"""
Header store for efficient SPV header queries.

Provides methods to build and query block headers for Rythmium clients.
Headers are stored in both database and in-memory cache for fast access.
"""

import struct
from typing import Optional

from rythmicd.db.dal import DAL
from rythmicd.db.models import Header
from rythmicd.logging import get_logger

logger = get_logger(__name__)

# Miqrochain header structure:
# - version: 4 bytes (uint32)
# - prev_hash: 32 bytes
# - merkle_root: 32 bytes
# - timestamp: 8 bytes (uint64) - NOTE: MIQ uses 64-bit timestamp
# - bits: 4 bytes (uint32)
# - nonce: 8 bytes (uint64) - NOTE: MIQ uses 64-bit nonce
# Total: 88 bytes (vs Bitcoin's 80 bytes)

MIQ_HEADER_SIZE = 88


def serialize_header(
    version: int,
    prev_hash: str,
    merkle_root: str,
    timestamp: int,
    bits: int,
    nonce: int,
) -> bytes:
    """
    Serialize a Miqrochain block header.

    Args:
        version: Block version
        prev_hash: Previous block hash (64-char hex, big-endian)
        merkle_root: Merkle root (64-char hex, big-endian)
        timestamp: Block timestamp (Unix seconds)
        bits: Difficulty target bits
        nonce: Proof-of-work nonce

    Returns:
        88-byte serialized header
    """
    # Convert hex hashes to bytes (reverse for internal little-endian)
    prev_hash_bytes = bytes.fromhex(prev_hash)[::-1]
    merkle_root_bytes = bytes.fromhex(merkle_root)[::-1]

    # Pack header fields
    header = struct.pack(
        "<I32s32sQIQ",  # Little-endian: uint32, 32 bytes, 32 bytes, uint64, uint32, uint64
        version,
        prev_hash_bytes,
        merkle_root_bytes,
        timestamp,
        bits,
        nonce,
    )

    return header


def deserialize_header(data: bytes) -> dict:
    """
    Deserialize a Miqrochain block header.

    Args:
        data: 88-byte serialized header

    Returns:
        Dictionary with header fields
    """
    if len(data) != MIQ_HEADER_SIZE:
        raise ValueError(f"Invalid header size: {len(data)}, expected {MIQ_HEADER_SIZE}")

    version, prev_hash, merkle_root, timestamp, bits, nonce = struct.unpack(
        "<I32s32sQIQ", data
    )

    return {
        "version": version,
        "prev_hash": prev_hash[::-1].hex(),  # Reverse back to big-endian
        "merkle_root": merkle_root[::-1].hex(),
        "timestamp": timestamp,
        "bits": bits,
        "nonce": nonce,
    }


class HeaderStore:
    """
    Manages block header storage and retrieval.

    Maintains an in-memory cache for fast header chain queries
    and persists to database for durability.
    """

    def __init__(self, dal: DAL, cache_size: int = 10000):
        """
        Initialize header store.

        Args:
            dal: Data access layer
            cache_size: Maximum headers to cache in memory
        """
        self._dal = dal
        self._cache_size = cache_size
        self._cache: dict[int, Header] = {}
        self._tip_height: int = -1

    async def load_cache(self) -> None:
        """Load recent headers into cache."""
        tip = await self._dal.get_tip_block()
        if not tip:
            logger.info("No blocks in database, header cache empty")
            return

        self._tip_height = tip.height

        # Load recent headers into cache
        start = max(0, tip.height - self._cache_size + 1)
        headers = await self._dal.get_headers_range(start, self._cache_size)

        for header in headers:
            self._cache[header.height] = header

        logger.info(
            "Header cache loaded",
            tip_height=self._tip_height,
            cached_headers=len(self._cache),
        )

    def add_to_cache(self, header: Header) -> None:
        """Add a header to the cache."""
        self._cache[header.height] = header
        if header.height > self._tip_height:
            self._tip_height = header.height

        # Evict old entries if cache is full
        if len(self._cache) > self._cache_size:
            oldest = min(self._cache.keys())
            del self._cache[oldest]

    def remove_from_cache(self, height: int) -> None:
        """Remove a header from cache (for reorgs)."""
        self._cache.pop(height, None)
        if height == self._tip_height:
            self._tip_height = height - 1

    async def get_header(self, height: int) -> Optional[Header]:
        """Get header by height."""
        # Check cache first
        if height in self._cache:
            return self._cache[height]

        # Fall back to database
        header = await self._dal.get_header_by_height(height)
        if header:
            # Add to cache if within recent range
            if height > self._tip_height - self._cache_size:
                self._cache[height] = header
        return header

    async def get_headers(
        self, start_height: int, count: int
    ) -> list[Header]:
        """
        Get a range of headers.

        Args:
            start_height: Starting height
            count: Number of headers to retrieve

        Returns:
            List of headers
        """
        headers: list[Header] = []

        for h in range(start_height, start_height + count):
            header = await self.get_header(h)
            if header:
                headers.append(header)
            else:
                break

        return headers

    async def get_header_hex(self, height: int) -> Optional[str]:
        """Get serialized header as hex string."""
        header = await self.get_header(height)
        if not header:
            return None
        return header.header_bytes.hex()

    async def get_headers_hex(
        self, start_height: int, count: int
    ) -> str:
        """
        Get concatenated headers as hex string.

        Used by blockchain.block.headers Rythmium protocol method.
        """
        headers = await self.get_headers(start_height, count)
        return "".join(h.header_bytes.hex() for h in headers)

    @property
    def tip_height(self) -> int:
        """Get the current tip height."""
        return self._tip_height

    async def get_tip_header(self) -> Optional[Header]:
        """Get the current tip header."""
        if self._tip_height < 0:
            return None
        return await self.get_header(self._tip_height)
