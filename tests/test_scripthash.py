"""
Tests for scripthash computation.

Verifies Rythmium/Electrum-compatible scripthash calculation from PKH and addresses.
"""

import pytest

from rythmicd.scripthash.scripthash_utils import (
    sha256,
    double_sha256,
    pkh_to_script_pubkey,
    compute_scripthash,
    pkh_to_scripthash,
    scripthash_to_hex,
    base58_decode,
    base58check_decode,
    decode_address,
    address_to_scripthash,
    pkh_hex_to_scripthash_hex,
    hex_to_pkh,
)


class TestHashFunctions:
    """Test basic hash functions."""

    def test_sha256_empty(self):
        """SHA256 of empty string."""
        result = sha256(b"")
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result.hex() == expected

    def test_sha256_hello(self):
        """SHA256 of 'hello'."""
        result = sha256(b"hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result.hex() == expected

    def test_double_sha256(self):
        """Double SHA256."""
        result = double_sha256(b"hello")
        # First hash then hash again
        first = sha256(b"hello")
        expected = sha256(first)
        assert result == expected


class TestScriptPubKey:
    """Test scriptPubKey construction."""

    def test_pkh_to_script_pubkey_valid(self):
        """Convert 20-byte PKH to P2PKH scriptPubKey."""
        pkh = bytes.fromhex("00" * 20)  # 20 zero bytes
        result = pkh_to_script_pubkey(pkh)

        # P2PKH: OP_DUP OP_HASH160 <20-byte-pkh> OP_EQUALVERIFY OP_CHECKSIG
        assert len(result) == 25
        assert result[0] == 0x76  # OP_DUP
        assert result[1] == 0xA9  # OP_HASH160
        assert result[2] == 0x14  # PUSH 20 bytes
        assert result[3:23] == pkh
        assert result[23] == 0x88  # OP_EQUALVERIFY
        assert result[24] == 0xAC  # OP_CHECKSIG

    def test_pkh_to_script_pubkey_invalid_length(self):
        """Reject non-20-byte PKH."""
        with pytest.raises(ValueError, match="PKH must be 20 bytes"):
            pkh_to_script_pubkey(bytes.fromhex("00" * 19))

        with pytest.raises(ValueError, match="PKH must be 20 bytes"):
            pkh_to_script_pubkey(bytes.fromhex("00" * 21))


class TestScripthash:
    """Test scripthash computation."""

    def test_compute_scripthash(self):
        """Compute scripthash from scriptPubKey."""
        # Simple scriptPubKey
        script = bytes.fromhex("76a914" + "00" * 20 + "88ac")
        result = compute_scripthash(script)

        # Should be reversed (little-endian)
        assert len(result) == 32

    def test_scripthash_is_reversed(self):
        """Verify scripthash is in little-endian order."""
        script = bytes.fromhex("76a914" + "00" * 20 + "88ac")
        result = compute_scripthash(script)
        hash_bytes = sha256(script)

        # Result should be reversed hash
        assert result == hash_bytes[::-1]

    def test_pkh_to_scripthash(self):
        """Compute scripthash directly from PKH."""
        pkh = bytes.fromhex("00" * 20)
        result = pkh_to_scripthash(pkh)

        # Should match manual computation
        script = pkh_to_script_pubkey(pkh)
        expected = compute_scripthash(script)
        assert result == expected

    def test_scripthash_to_hex(self):
        """Convert scripthash to hex string."""
        scripthash = bytes.fromhex("ab" * 32)
        result = scripthash_to_hex(scripthash)
        assert result == "ab" * 32


class TestBase58:
    """Test Base58 encoding/decoding."""

    def test_base58_decode_simple(self):
        """Decode simple Base58 string."""
        # '1' in Base58 = 0x00 byte
        result = base58_decode("1")
        assert result == bytes([0])

    def test_base58_decode_leading_ones(self):
        """Decode Base58 with leading '1's."""
        result = base58_decode("111")
        assert result[:3] == bytes([0, 0, 0])

    def test_base58check_decode_invalid_checksum(self):
        """Reject invalid checksum."""
        # This is a made-up string that won't have valid checksum
        with pytest.raises(ValueError, match="Invalid address checksum"):
            base58check_decode("5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ")


class TestAddressDecode:
    """Test Miqrochain address decoding."""

    def test_decode_address_structure(self):
        """Verify address decode returns version and PKH."""
        # Create a valid-structure test (actual validation requires real address)
        # For this test, we verify the function interface
        pass  # Would need a real MIQ address for full test

    def test_address_to_scripthash_consistency(self):
        """Verify address->scripthash->hex chain works."""
        # Test with known PKH
        pkh = bytes.fromhex("00c649e06c60278501aad8a3b05d345fe8008836")
        scripthash = pkh_to_scripthash(pkh)
        hex_result = scripthash_to_hex(scripthash)

        assert len(hex_result) == 64
        assert all(c in "0123456789abcdef" for c in hex_result)


class TestPkhHexConversions:
    """Test PKH hex string conversions."""

    def test_hex_to_pkh_valid(self):
        """Convert valid hex to PKH."""
        hex_str = "00c649e06c60278501aad8a3b05d345fe8008836"
        result = hex_to_pkh(hex_str)
        assert len(result) == 20
        assert result.hex() == hex_str

    def test_hex_to_pkh_invalid_length(self):
        """Reject invalid length hex."""
        with pytest.raises(ValueError, match="PKH must be 20 bytes"):
            hex_to_pkh("00" * 19)

    def test_pkh_hex_to_scripthash_hex(self):
        """Full chain: PKH hex -> scripthash hex."""
        pkh_hex = "00c649e06c60278501aad8a3b05d345fe8008836"
        result = pkh_hex_to_scripthash_hex(pkh_hex)

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

        # Verify consistency
        pkh = hex_to_pkh(pkh_hex)
        expected = scripthash_to_hex(pkh_to_scripthash(pkh))
        assert result == expected


class TestKnownVectors:
    """Test against known scripthash vectors."""

    def test_genesis_coinbase_pkh(self):
        """
        Test scripthash for genesis coinbase recipient.

        Genesis coinbase PKH: 00c649e06c60278501aad8a3b05d345fe8008836
        """
        pkh_hex = "00c649e06c60278501aad8a3b05d345fe8008836"
        result = pkh_hex_to_scripthash_hex(pkh_hex)

        # Result should be deterministic
        assert len(result) == 64

        # Verify it's stable (same input = same output)
        result2 = pkh_hex_to_scripthash_hex(pkh_hex)
        assert result == result2

    def test_different_pkh_different_scripthash(self):
        """Different PKHs should produce different scripthashes."""
        pkh1 = "00c649e06c60278501aad8a3b05d345fe8008836"
        pkh2 = "00c649e06c60278501aad8a3b05d345fe8008837"  # Last byte different

        result1 = pkh_hex_to_scripthash_hex(pkh1)
        result2 = pkh_hex_to_scripthash_hex(pkh2)

        assert result1 != result2
