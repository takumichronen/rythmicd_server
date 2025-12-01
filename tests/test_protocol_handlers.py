"""
Tests for Rythmium protocol handlers.

Tests the JSON-RPC method handlers with mocked dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from rythmicd.config.settings import RythmiumServerConfig
from rythmicd.protocol.handlers import RythmiumHandlers
from rythmicd.db.models import Header, UTXO, ScripthashHistory


class MockClientSession:
    """Mock client session for testing."""

    def __init__(self):
        self.id = "test-client-123"
        self.subscribed_headers = False
        self.subscribed_scripthashes = set()
        self.protocol_version = "1.4"
        self.client_name = ""
        self.request_count = 0


@pytest.fixture
def mock_rpc():
    """Create mock RPC client."""
    rpc = AsyncMock()
    rpc.get_raw_transaction = AsyncMock(return_value={"hex": "0100..."})
    rpc.send_raw_transaction = AsyncMock(return_value={"result": "txid123"})
    return rpc


@pytest.fixture
def mock_dal():
    """Create mock DAL."""
    dal = AsyncMock()
    dal.get_balance_for_scripthash = AsyncMock(return_value={"confirmed": 1000000, "unconfirmed": 0})
    dal.get_scripthash_history = AsyncMock(return_value=[])
    dal.get_utxos_for_scripthash = AsyncMock(return_value=[])
    dal.get_scripthash_status = AsyncMock(return_value="")
    dal.get_transaction = AsyncMock(return_value=None)
    return dal


@pytest.fixture
def mock_header_store():
    """Create mock header store."""
    store = AsyncMock()
    store.tip_height = 100
    store.get_header = AsyncMock(return_value=Header(
        height=100,
        hash="ab" * 32,
        prev_hash="cd" * 32,
        header_bytes=bytes(88),
    ))
    store.get_header_hex = AsyncMock(return_value="00" * 88)
    store.get_headers_hex = AsyncMock(return_value="00" * 88)
    store.get_tip_header = AsyncMock(return_value=Header(
        height=100,
        hash="ab" * 32,
        prev_hash="cd" * 32,
        header_bytes=bytes(88),
    ))
    return store


@pytest.fixture
def mock_mempool():
    """Create mock mempool tracker."""
    mempool = MagicMock()
    mempool.get_scripthash_mempool = MagicMock(return_value=[])
    mempool.get_scripthash_unconfirmed_balance = MagicMock(return_value=0)
    mempool.get_fee_histogram = MagicMock(return_value=[])
    mempool.get_transaction = MagicMock(return_value=None)
    mempool.get_stats = MagicMock(return_value={"avg_fee_rate": 1.0})
    return mempool


@pytest.fixture
def config():
    """Create test config."""
    return RythmiumServerConfig(
        banner="Test Server",
        protocol_min="1.4",
        protocol_max="1.4.2",
        max_subscriptions_per_client=1000,
    )


@pytest.fixture
def handlers(mock_rpc, mock_dal, mock_header_store, mock_mempool, config):
    """Create handlers with mocked dependencies."""
    return RythmiumHandlers(mock_rpc, mock_dal, mock_header_store, mock_mempool, config)


class TestServerMethods:
    """Test server.* methods."""

    @pytest.mark.asyncio
    async def test_server_version(self, handlers):
        """Test server.version negotiation."""
        client = MockClientSession()
        result = await handlers.server_version(client, ["TestWallet/1.0", "1.4"])

        assert len(result) == 2
        assert "rythmicd" in result[0]
        assert result[1] == "1.4.2"
        assert client.client_name == "TestWallet/1.0"

    @pytest.mark.asyncio
    async def test_server_banner(self, handlers):
        """Test server.banner returns banner."""
        client = MockClientSession()
        result = await handlers.server_banner(client, [])

        assert result == "Test Server"

    @pytest.mark.asyncio
    async def test_server_ping(self, handlers):
        """Test server.ping returns None."""
        client = MockClientSession()
        result = await handlers.server_ping(client, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_server_features(self, handlers):
        """Test server.features returns capabilities."""
        client = MockClientSession()
        result = await handlers.server_features(client, [])

        assert "genesis_hash" in result
        assert "protocol_min" in result
        assert "protocol_max" in result
        assert result["hash_function"] == "sha256"


class TestHeaderMethods:
    """Test blockchain.headers.* methods."""

    @pytest.mark.asyncio
    async def test_headers_subscribe(self, handlers):
        """Test blockchain.headers.subscribe."""
        client = MockClientSession()
        result = await handlers.blockchain_headers_subscribe(client, [])

        assert client.subscribed_headers is True
        assert "height" in result
        assert "hex" in result
        assert result["height"] == 100

    @pytest.mark.asyncio
    async def test_block_header(self, handlers):
        """Test blockchain.block.header."""
        client = MockClientSession()
        result = await handlers.blockchain_block_header(client, [50])

        assert isinstance(result, str)
        assert len(result) == 176  # 88 bytes in hex

    @pytest.mark.asyncio
    async def test_block_headers(self, handlers):
        """Test blockchain.block.headers."""
        client = MockClientSession()
        result = await handlers.blockchain_block_headers(client, [0, 10])

        assert "count" in result
        assert "hex" in result
        assert "max" in result
        assert result["max"] == 2016


class TestScripthashMethods:
    """Test blockchain.scripthash.* methods."""

    @pytest.mark.asyncio
    async def test_scripthash_subscribe(self, handlers):
        """Test blockchain.scripthash.subscribe."""
        client = MockClientSession()
        scripthash = "ab" * 32

        result = await handlers.blockchain_scripthash_subscribe(client, [scripthash])

        assert scripthash in client.subscribed_scripthashes

    @pytest.mark.asyncio
    async def test_scripthash_subscribe_invalid(self, handlers):
        """Test blockchain.scripthash.subscribe with invalid scripthash."""
        client = MockClientSession()

        with pytest.raises(Exception, match="Invalid scripthash"):
            await handlers.blockchain_scripthash_subscribe(client, ["invalid"])

    @pytest.mark.asyncio
    async def test_scripthash_unsubscribe(self, handlers):
        """Test blockchain.scripthash.unsubscribe."""
        client = MockClientSession()
        scripthash = "ab" * 32
        client.subscribed_scripthashes.add(scripthash)

        result = await handlers.blockchain_scripthash_unsubscribe(client, [scripthash])

        assert result is True
        assert scripthash not in client.subscribed_scripthashes

    @pytest.mark.asyncio
    async def test_scripthash_get_balance(self, handlers, mock_dal):
        """Test blockchain.scripthash.get_balance."""
        client = MockClientSession()
        scripthash = "ab" * 32

        result = await handlers.blockchain_scripthash_get_balance(client, [scripthash])

        assert "confirmed" in result
        assert "unconfirmed" in result
        assert result["confirmed"] == 1000000

    @pytest.mark.asyncio
    async def test_scripthash_get_history(self, handlers, mock_dal):
        """Test blockchain.scripthash.get_history."""
        mock_dal.get_scripthash_history = AsyncMock(return_value=[
            ScripthashHistory(
                scripthash="ab" * 32,
                txid="cd" * 32,
                height=50,
                tx_pos=0,
                value_delta=1000,
            ),
        ])

        client = MockClientSession()
        scripthash = "ab" * 32

        result = await handlers.blockchain_scripthash_get_history(client, [scripthash])

        assert len(result) == 1
        assert result[0]["tx_hash"] == "cd" * 32
        assert result[0]["height"] == 50

    @pytest.mark.asyncio
    async def test_scripthash_listunspent(self, handlers, mock_dal):
        """Test blockchain.scripthash.listunspent."""
        mock_dal.get_utxos_for_scripthash = AsyncMock(return_value=[
            UTXO(
                scripthash="ab" * 32,
                txid="cd" * 32,
                vout=0,
                value=1000000,
                height=50,
                pkh="00" * 20,
            ),
        ])

        client = MockClientSession()
        scripthash = "ab" * 32

        result = await handlers.blockchain_scripthash_listunspent(client, [scripthash])

        assert len(result) == 1
        assert result[0]["tx_hash"] == "cd" * 32
        assert result[0]["tx_pos"] == 0
        assert result[0]["value"] == 1000000


class TestTransactionMethods:
    """Test blockchain.transaction.* methods."""

    @pytest.mark.asyncio
    async def test_transaction_get_from_rpc(self, handlers, mock_rpc):
        """Test blockchain.transaction.get falls back to RPC."""
        client = MockClientSession()
        txid = "ab" * 32

        result = await handlers.blockchain_transaction_get(client, [txid])

        assert result == "0100..."  # From mock RPC response

    @pytest.mark.asyncio
    async def test_transaction_broadcast(self, handlers, mock_rpc):
        """Test blockchain.transaction.broadcast."""
        client = MockClientSession()
        raw_tx = "0100000001..."

        result = await handlers.blockchain_transaction_broadcast(client, [raw_tx])

        assert result == "txid123"
        mock_rpc.send_raw_transaction.assert_called_once_with(raw_tx)

    @pytest.mark.asyncio
    async def test_transaction_broadcast_missing_tx(self, handlers):
        """Test blockchain.transaction.broadcast with missing tx."""
        client = MockClientSession()

        with pytest.raises(Exception, match="Missing transaction"):
            await handlers.blockchain_transaction_broadcast(client, [])


class TestMempoolMethods:
    """Test mempool.* methods."""

    @pytest.mark.asyncio
    async def test_get_fee_histogram(self, handlers, mock_mempool):
        """Test mempool.get_fee_histogram."""
        mock_mempool.get_fee_histogram = MagicMock(return_value=[[10.0, 1000], [5.0, 2000]])

        client = MockClientSession()
        result = await handlers.mempool_get_fee_histogram(client, [])

        assert len(result) == 2
        assert result[0] == [10.0, 1000]


class TestMethodDispatch:
    """Test method dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_known_method(self, handlers):
        """Test dispatching a known method."""
        client = MockClientSession()
        result = await handlers.handle(client, "server.ping", [])

        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_unknown_method(self, handlers):
        """Test dispatching an unknown method raises error."""
        client = MockClientSession()

        with pytest.raises(Exception, match="Unknown method"):
            await handlers.handle(client, "unknown.method", [])

    @pytest.mark.asyncio
    async def test_dispatch_with_dict_params(self, handlers):
        """Test dispatching with dict params (converted to list)."""
        client = MockClientSession()
        result = await handlers.handle(client, "server.version", {"client": "Test", "version": "1.4"})

        assert len(result) == 2
