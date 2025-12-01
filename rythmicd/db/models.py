"""
Database models for rythmicd.

Defines dataclasses representing the database schema for blocks, transactions,
UTXOs, scripthash history, and headers.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Block:
    """Block record."""

    height: int
    hash: str  # 64-char hex
    prev_hash: str  # 64-char hex
    merkle_root: str  # 64-char hex
    timestamp: int  # Unix timestamp
    version: int
    bits: int
    nonce: int
    tx_count: int
    size: int = 0


@dataclass
class Header:
    """Block header record for SPV clients."""

    height: int
    hash: str  # 64-char hex
    prev_hash: str  # 64-char hex
    header_bytes: bytes  # 80-byte serialized header


@dataclass
class TxInput:
    """Transaction input (for internal use during parsing)."""

    prev_txid: str  # 64-char hex
    prev_vout: int
    scriptsig: bytes
    pubkey: bytes


@dataclass
class TxOutput:
    """Transaction output (for internal use during parsing)."""

    value: int  # In miqrons (satoshi-equivalent)
    pkh: str  # 40-char hex (20-byte public key hash)
    scripthash: str  # 64-char hex (Rythmium/Electrum scripthash)


@dataclass
class Transaction:
    """Transaction record."""

    txid: str  # 64-char hex
    block_hash: str  # 64-char hex (empty for mempool)
    block_height: int  # -1 for mempool
    index_in_block: int
    raw_tx: str  # hex-encoded raw transaction
    fee: int  # In miqrons
    size: int  # Bytes
    is_coinbase: bool
    version: int = 1
    locktime: int = 0
    inputs: list[TxInput] = field(default_factory=list)
    outputs: list[TxOutput] = field(default_factory=list)


@dataclass
class UTXO:
    """Unspent transaction output record."""

    scripthash: str  # 64-char hex
    txid: str  # 64-char hex
    vout: int
    value: int  # In miqrons
    height: int  # Block height when created
    pkh: str  # 40-char hex
    is_coinbase: bool = False
    is_spent: bool = False
    spent_by_txid: Optional[str] = None
    spent_height: Optional[int] = None


@dataclass
class ScripthashHistory:
    """
    Scripthash history entry.

    Each entry represents a transaction that affected a scripthash,
    either by creating an output (positive delta) or spending one (negative delta).
    """

    scripthash: str  # 64-char hex
    txid: str  # 64-char hex
    height: int  # Block height (-1 for mempool)
    tx_pos: int  # Position in block (for ordering)
    value_delta: int  # Positive for outputs, negative for spends


@dataclass
class ScripthashStatus:
    """
    Current status hash for a scripthash.

    Used for Rythmium protocol subscription notifications.
    The status is a hash of all transaction hashes affecting this scripthash.
    """

    scripthash: str  # 64-char hex
    status_hash: str  # 64-char hex (SHA256 of tx hashes)
    confirmed_balance: int  # Sum of confirmed UTXOs
    unconfirmed_balance: int  # Sum of unconfirmed UTXOs
