# Rythmicd Architecture

## Overview

Rythmicd is a Rythmium-compatible server for Miqrochain that indexes the blockchain and provides a Rythmium protocol (Electrum-compatible) JSON-RPC over TCP for wallet clients.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Miqrod Node   │────▶│    Rythmicd     │────▶│   Rythmium      │
│   (Full Node)   │ RPC │   (Indexer)     │ TCP │   Wallets       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │   PostgreSQL    │
                        │   (Index DB)    │
                        └─────────────────┘
```

## Components

### 1. RPC Client (`rythmicd/rpc/`)

The RPC client communicates with the miqrod full node via JSON-RPC over HTTP.

**Key Features:**
- Async HTTP client using aiohttp
- Cookie-based or user/password authentication
- Automatic retries with exponential backoff
- Comprehensive method wrappers for blockchain queries

**Primary Methods Used:**
- `getbestblockhash` - Get tip block hash
- `getblockhash(height)` - Get block hash at height
- `getblock(hash)` - Get block with transactions
- `getrawtransaction(txid)` - Get transaction details
- `getrawmempool` - List mempool transactions
- `sendrawtransaction(hex)` - Broadcast transaction

### 2. Database Layer (`rythmicd/db/`)

PostgreSQL-based storage for blockchain index data.

**Schema:**
```
blocks          - Block headers and metadata
headers         - Serialized headers for SPV
transactions    - Transaction records
utxos           - Unspent transaction outputs
scripthash_history - Per-scripthash transaction history
metadata        - Indexer state (e.g., indexed height)
```

**DAL (Data Access Layer):**
- Async operations using asyncpg
- Batch operations for efficient indexing
- Transaction support for atomic updates
- Rollback support for reorg handling

### 3. Scripthash Utilities (`rythmicd/scripthash/`)

Implements Rythmium/Electrum-compatible scripthash computation.

**Scripthash Definition:**
```
scripthash = SHA256(scriptPubKey)[reversed]
```

For Miqrochain P2PKH addresses:
```
scriptPubKey = OP_DUP OP_HASH160 <20-byte-pkh> OP_EQUALVERIFY OP_CHECKSIG
             = 76 a9 14 <pkh> 88 ac
```

The scripthash is returned as a little-endian hex string (64 characters).

### 4. Block Indexer (`rythmicd/indexer/`)

Synchronizes blockchain data from miqrod to PostgreSQL.

**Components:**
- `BlockIndexer` - Main sync loop and block processing
- `ReorgHandler` - Chain reorganization detection and rollback
- `MempoolTracker` - Unconfirmed transaction tracking

**Sync Process:**
1. Compare indexed height vs node height
2. Check for reorgs (hash mismatch at tip)
3. If reorg detected, roll back to common ancestor
4. Index new blocks in batches
5. Update UTXOs and scripthash history
6. Notify protocol layer of changes

**Reorg Handling:**
1. Detect hash mismatch between local and node chain
2. Walk back to find common ancestor
3. Roll back orphaned blocks:
   - Delete block/transaction records
   - Unspend spent UTXOs
   - Delete created UTXOs
   - Remove scripthash history entries
4. Resume normal indexing on new chain

### 5. Header Store (`rythmicd/headers/`)

Manages block headers for SPV client support.

**Features:**
- In-memory LRU cache for recent headers
- Efficient range queries for `blockchain.block.headers`
- Header serialization/deserialization

**Miqrochain Header Format (88 bytes):**
```
version      - 4 bytes (uint32)
prev_hash    - 32 bytes
merkle_root  - 32 bytes
timestamp    - 8 bytes (uint64) - Note: MIQ uses 64-bit
bits         - 4 bytes (uint32)
nonce        - 8 bytes (uint64) - Note: MIQ uses 64-bit
```

### 6. Protocol Server (`rythmicd/protocol/`)

Rythmium protocol (Electrum-compatible) JSON-RPC server over TCP.

**Server (`server.py`):**
- Async TCP server using asyncio
- Per-client session management
- Subscription tracking (headers, scripthashes)
- Notification dispatch

**Handlers (`handlers.py`):**
- Method dispatch for Rythmium protocol RPC calls
- Balance, history, UTXO queries
- Transaction broadcast
- Header subscriptions

## Data Flow

### Block Indexing Flow

```
miqrod ──RPC──▶ BlockIndexer ──▶ DAL ──▶ PostgreSQL
                     │
                     ▼
              HeaderStore (cache)
                     │
                     ▼
              RythmiumServer (notify)
```

1. `BlockIndexer` polls miqrod for new blocks
2. Fetches block data via RPC
3. Parses transactions, extracts UTXOs and scripthash history
4. Batch inserts to PostgreSQL via DAL
5. Updates header cache
6. Notifies subscribed clients

### Client Query Flow

```
Wallet ──TCP──▶ RythmiumServer ──▶ Handlers
                                      │
                      ┌───────────────┼───────────────┐
                      ▼               ▼               ▼
                    DAL          HeaderStore      Mempool
                      │               │               │
                      ▼               ▼               ▼
                 PostgreSQL       (cache)        (in-memory)
```

1. Client sends JSON-RPC request
2. Server dispatches to appropriate handler
3. Handler queries:
   - DAL for confirmed data
   - HeaderStore for headers
   - MempoolTracker for unconfirmed data
4. Response sent to client

### Subscription Notification Flow

```
BlockIndexer ──(new block)──▶ RythmiumServer
                                   │
                         ┌─────────┴─────────┐
                         ▼                   ▼
               Header Subscribers    Scripthash Subscribers
                         │                   │
                         ▼                   ▼
                    Wallet A             Wallet B
```

## Configuration

Configuration is loaded from (in order of precedence):
1. CLI arguments
2. Environment variables (`RYTHMICD_*`)
3. YAML config file
4. Default values

See `config.yaml.example` for all options.

## Threading Model

Rythmicd is fully async using Python's asyncio:

- **Main loop**: Coordinates startup/shutdown
- **Indexer task**: Block synchronization
- **Mempool task**: Mempool polling
- **Server tasks**: One per client connection

All database and RPC operations are non-blocking.

## Security Considerations

1. **RPC Authentication**: Use cookie or user/password auth
2. **Client Limits**: Max connections, subscriptions, message size
3. **Input Validation**: Validate all client inputs
4. **Non-root Container**: Docker runs as unprivileged user
5. **TLS Support**: SSL/TLS for encrypted connections (via config or reverse proxy)
