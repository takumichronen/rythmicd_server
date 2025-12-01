"""
Scripthash computation utilities for Rythmium/Electrum compatibility.

Implements the Rythmium/Electrum scripthash convention:
  scripthash = SHA256(scriptPubKey)
  Returned as little-endian hex string (reversed bytes)

For Miqrochain P2PKH addresses:
  scriptPubKey = OP_DUP OP_HASH160 <20-byte-pkh> OP_EQUALVERIFY OP_CHECKSIG
  Which is: 76 a9 14 <pkh> 88 ac
"""

import hashlib
from typing import Final

# Bitcoin script opcodes used in P2PKH
OP_DUP: Final[int] = 0x76
OP_HASH160: Final[int] = 0xA9
OP_EQUALVERIFY: Final[int] = 0x88
OP_CHECKSIG: Final[int] = 0xAC
PUSH_20: Final[int] = 0x14  # Push 20 bytes

# Base58 alphabet (Bitcoin/Miqrochain style)
BASE58_ALPHABET: Final[str] = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def sha256(data: bytes) -> bytes:
    """Compute SHA256 hash."""
    return hashlib.sha256(data).digest()


def double_sha256(data: bytes) -> bytes:
    """Compute double SHA256 (used in Bitcoin/MIQ addresses)."""
    return sha256(sha256(data))


def pkh_to_script_pubkey(pkh: bytes) -> bytes:
    """
    Convert a 20-byte public key hash to a P2PKH scriptPubKey.

    Args:
        pkh: 20-byte HASH160 of public key

    Returns:
        25-byte P2PKH scriptPubKey: OP_DUP OP_HASH160 <pkh> OP_EQUALVERIFY OP_CHECKSIG
    """
    if len(pkh) != 20:
        raise ValueError(f"PKH must be 20 bytes, got {len(pkh)}")

    return bytes([OP_DUP, OP_HASH160, PUSH_20]) + pkh + bytes([OP_EQUALVERIFY, OP_CHECKSIG])


def compute_scripthash(script_pubkey: bytes) -> bytes:
    """
    Compute Rythmium/Electrum-style scripthash from a scriptPubKey.

    The scripthash is SHA256(scriptPubKey) in little-endian byte order.

    Args:
        script_pubkey: The scriptPubKey bytes

    Returns:
        32-byte scripthash (little-endian / reversed)
    """
    hash_bytes = sha256(script_pubkey)
    # Rythmium/Electrum uses little-endian (reversed) representation
    return hash_bytes[::-1]


def scripthash_to_hex(scripthash: bytes) -> str:
    """
    Convert scripthash bytes to hex string.

    Args:
        scripthash: 32-byte scripthash

    Returns:
        64-character hex string
    """
    return scripthash.hex()


def pkh_to_scripthash(pkh: bytes) -> bytes:
    """
    Compute scripthash directly from a public key hash.

    Args:
        pkh: 20-byte public key hash

    Returns:
        32-byte scripthash
    """
    script_pubkey = pkh_to_script_pubkey(pkh)
    return compute_scripthash(script_pubkey)


def pkh_to_scripthash_hex(pkh: bytes) -> str:
    """
    Compute scripthash hex string from a public key hash.

    Args:
        pkh: 20-byte public key hash

    Returns:
        64-character scripthash hex string
    """
    return scripthash_to_hex(pkh_to_scripthash(pkh))


def base58_decode(s: str) -> bytes:
    """
    Decode a Base58-encoded string.

    Args:
        s: Base58 string

    Returns:
        Decoded bytes
    """
    # Count leading '1's (which represent zero bytes)
    n = 0
    for c in s:
        if c == "1":
            n += 1
        else:
            break

    # Decode the rest
    num = 0
    for c in s:
        num = num * 58 + BASE58_ALPHABET.index(c)

    # Convert to bytes
    result = []
    while num > 0:
        result.append(num % 256)
        num //= 256

    return bytes(n) + bytes(reversed(result))


def base58check_decode(s: str) -> tuple[int, bytes]:
    """
    Decode a Base58Check-encoded address.

    Args:
        s: Base58Check encoded string (MIQ address)

    Returns:
        Tuple of (version_byte, payload)

    Raises:
        ValueError: If checksum verification fails
    """
    decoded = base58_decode(s)

    if len(decoded) < 5:
        raise ValueError("Address too short")

    # Split into payload and checksum
    payload_with_version = decoded[:-4]
    checksum = decoded[-4:]

    # Verify checksum
    expected_checksum = double_sha256(payload_with_version)[:4]
    if checksum != expected_checksum:
        raise ValueError("Invalid address checksum")

    version = payload_with_version[0]
    payload = payload_with_version[1:]

    return version, payload


def decode_address(address: str) -> tuple[int, bytes]:
    """
    Decode a Miqrochain address.

    Args:
        address: MIQ address string

    Returns:
        Tuple of (version_byte, 20-byte pkh)

    Raises:
        ValueError: If address is invalid
    """
    version, pkh = base58check_decode(address)

    if len(pkh) != 20:
        raise ValueError(f"Invalid address payload length: {len(pkh)}, expected 20")

    return version, pkh


def address_to_scripthash(address: str) -> bytes:
    """
    Convert a Miqrochain address to scripthash.

    Args:
        address: MIQ address string

    Returns:
        32-byte scripthash
    """
    _, pkh = decode_address(address)
    return pkh_to_scripthash(pkh)


def address_to_scripthash_hex(address: str) -> str:
    """
    Convert a Miqrochain address to scripthash hex string.

    Args:
        address: MIQ address string

    Returns:
        64-character scripthash hex string
    """
    return scripthash_to_hex(address_to_scripthash(address))


def hex_to_pkh(hex_str: str) -> bytes:
    """
    Convert hex string to PKH bytes.

    Args:
        hex_str: 40-character hex string

    Returns:
        20-byte PKH
    """
    pkh = bytes.fromhex(hex_str)
    if len(pkh) != 20:
        raise ValueError(f"PKH must be 20 bytes, got {len(pkh)}")
    return pkh


def pkh_hex_to_scripthash_hex(pkh_hex: str) -> str:
    """
    Convert PKH hex string to scripthash hex string.

    This is the most common operation during indexing, as MIQ stores
    outputs with bare PKH values.

    Args:
        pkh_hex: 40-character PKH hex string

    Returns:
        64-character scripthash hex string
    """
    pkh = hex_to_pkh(pkh_hex)
    return pkh_to_scripthash_hex(pkh)
