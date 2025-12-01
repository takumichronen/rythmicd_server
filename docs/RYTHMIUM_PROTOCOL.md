# Rythmium Protocol Reference

Rythmicd implements the Rythmium protocol (Electrum-compatible, version 1.4.x) over TCP with JSON-RPC 2.0.

## Connection

- **Default TCP Port**: 50001
- **Default SSL Port**: 50002 (if configured)
- **Protocol**: JSON-RPC 2.0 over newline-delimited TCP

## Supported Methods

### Server Methods

#### server.version

Negotiate protocol version with the server.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server.version",
  "params": ["client_name", "protocol_version"]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": ["rythmicd 0.1.0", "1.4.2"]
}
```

#### server.banner

Get server banner message.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server.banner",
  "params": []
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "Rythmicd - Miqrochain Rythmium Server"
}
```

#### server.ping

Ping the server.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server.ping",
  "params": []
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": null
}
```

#### server.features

Get server capabilities.

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "genesis_hash": "00000000a5e8a7eb02a83fb9693bc2dccbf14ee69d67315c1f151a25cb43fce8",
    "hash_function": "sha256",
    "server_version": "rythmicd 0.1.0",
    "protocol_min": "1.4",
    "protocol_max": "1.4.2",
    "pruning": null
  }
}
```

### Blockchain Methods

#### blockchain.headers.subscribe

Subscribe to new block headers.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.headers.subscribe",
  "params": []
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "height": 12345,
    "hex": "010000..."
  }
}
```

**Notification (on new block):**
```json
{
  "jsonrpc": "2.0",
  "method": "blockchain.headers.subscribe",
  "params": [{"height": 12346, "hex": "010000..."}]
}
```

#### blockchain.block.header

Get block header by height.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.block.header",
  "params": [12345]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "010000..."
}
```

#### blockchain.block.headers

Get a range of block headers.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.block.headers",
  "params": [12345, 10]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "count": 10,
    "hex": "010000...",
    "max": 2016
  }
}
```

#### blockchain.estimatefee

Estimate fee for confirmation within N blocks.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.estimatefee",
  "params": [6]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": 0.00001
}
```

### Scripthash Methods

The scripthash is computed as:
```
scripthash = SHA256(scriptPubKey) [reversed to little-endian]
```

For P2PKH addresses:
```
scriptPubKey = OP_DUP OP_HASH160 <pkh> OP_EQUALVERIFY OP_CHECKSIG
```

#### blockchain.scripthash.subscribe

Subscribe to scripthash status changes.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.subscribe",
  "params": ["ab1234...64chars"]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "status_hash_or_null"
}
```

**Notification (on change):**
```json
{
  "jsonrpc": "2.0",
  "method": "blockchain.scripthash.subscribe",
  "params": ["ab1234...", "new_status_hash"]
}
```

#### blockchain.scripthash.unsubscribe

Unsubscribe from scripthash.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.unsubscribe",
  "params": ["ab1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": true
}
```

#### blockchain.scripthash.get_balance

Get balance for scripthash.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.get_balance",
  "params": ["ab1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "confirmed": 100000000,
    "unconfirmed": 0
  }
}
```

Values are in miqrons (1 MIQ = 100,000,000 miqrons).

#### blockchain.scripthash.get_history

Get transaction history for scripthash.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.get_history",
  "params": ["ab1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {"tx_hash": "cd5678...", "height": 12345},
    {"tx_hash": "ef9012...", "height": 12400}
  ]
}
```

Height of 0 or -1 indicates unconfirmed transaction.

#### blockchain.scripthash.get_mempool

Get unconfirmed transactions for scripthash.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.get_mempool",
  "params": ["ab1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {"tx_hash": "cd5678...", "height": 0, "fee": 1000}
  ]
}
```

#### blockchain.scripthash.listunspent

Get unspent outputs for scripthash.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.scripthash.listunspent",
  "params": ["ab1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "tx_hash": "cd5678...",
      "tx_pos": 0,
      "height": 12345,
      "value": 100000000
    }
  ]
}
```

### Transaction Methods

#### blockchain.transaction.get

Get raw transaction by txid.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.transaction.get",
  "params": ["cd5678..."]
}
```

**Response (raw):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "0100000001..."
}
```

**Request (verbose):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.transaction.get",
  "params": ["cd5678...", true]
}
```

**Response (verbose):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "txid": "cd5678...",
    "hash": "cd5678...",
    "hex": "0100000001...",
    "blockhash": "ab1234...",
    "confirmations": 100
  }
}
```

#### blockchain.transaction.broadcast

Broadcast a raw transaction.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.transaction.broadcast",
  "params": ["0100000001..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "cd5678..."
}
```

Returns the txid on success.

#### blockchain.transaction.get_merkle

Get merkle proof for transaction.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "blockchain.transaction.get_merkle",
  "params": ["cd5678...", 12345]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "merkle": ["..."],
    "block_height": 12345,
    "pos": 2
  }
}
```

### Mempool Methods

#### mempool.get_fee_histogram

Get fee histogram for the mempool.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "mempool.get_fee_histogram",
  "params": []
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    [10.0, 100000],
    [5.0, 200000],
    [1.0, 500000]
  ]
}
```

Each entry is [fee_rate, cumulative_vsize].

## Error Codes

| Code | Message |
|------|---------|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| -32000 | Server error |

## Batch Requests

Multiple requests can be sent in a single message:

```json
[
  {"jsonrpc": "2.0", "id": 1, "method": "server.ping", "params": []},
  {"jsonrpc": "2.0", "id": 2, "method": "server.version", "params": ["wallet", "1.4"]}
]
```

Response:

```json
[
  {"jsonrpc": "2.0", "id": 1, "result": null},
  {"jsonrpc": "2.0", "id": 2, "result": ["rythmicd 0.1.0", "1.4.2"]}
]
```
