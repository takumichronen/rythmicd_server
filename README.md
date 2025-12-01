# Rythmicd

**Rythmium-compatible server for Miqrochain** - enables Rythmium wallets and other compatible clients to interact with the Miqrochain network.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

Rythmicd is a production-grade indexing server that:

- Connects to a **local miqrod node via RPC**
- Indexes the full blockchain in **PostgreSQL**, keyed by **scripthash**
- Tracks blocks, transactions, UTXOs, scripthash history, and mempool
- Provides a **Rythmium protocol (Electrum-compatible) JSON-RPC over TCP** for wallet clients
- Handles **chain reorganizations** correctly
- Supports **thousands of concurrent wallet connections**

## Features

- Full Rythmium protocol support (Electrum-compatible, v1.4.x)
- Real-time block and transaction notifications
- Mempool tracking with fee estimation
- Scripthash-based address indexing
- Efficient reorg handling
- SSL/TLS support
- Docker deployment ready

## Quick Start

### Using Docker (Recommended)

```bash
# Clone repository
git clone https://github.com/takumichronen/rythmicd_server.git
cd rythmicd_server

# Configure (edit as needed)
cp config.yaml.example config.yaml

# Start services
docker compose up -d

# Check logs
docker compose logs -f rythmicd
```

### Native Installation

```bash
# Requirements: Python 3.11+, PostgreSQL 12+

# Install
pip install -e .

# Create database
createdb rythmicd

# Run
rythmicd --config config.yaml
```

## Configuration

Rythmicd can be configured via:

1. **YAML config file** (`config.yaml`)
2. **Environment variables** (`RYTHMICD_*`)
3. **CLI arguments**

### Environment Variables

```bash
# Miqrod RPC
RYTHMICD_RPC_HOST=127.0.0.1
RYTHMICD_RPC_PORT=9834
RYTHMICD_RPC_USERNAME=
RYTHMICD_RPC_PASSWORD=

# Database
RYTHMICD_DB_HOST=127.0.0.1
RYTHMICD_DB_PORT=5432
RYTHMICD_DB_NAME=rythmicd
RYTHMICD_DB_USERNAME=rythmicd
RYTHMICD_DB_PASSWORD=secret

# Rythmium Server
RYTHMICD_RYTHMIUM_HOST=0.0.0.0
RYTHMICD_RYTHMIUM_PORT=50001

# Logging
RYTHMICD_LOG_LEVEL=INFO
```

### CLI Options

```bash
rythmicd --help

Options:
  -c, --config PATH      YAML configuration file
  --rpc-host HOST        Miqrod RPC host
  --rpc-port PORT        Miqrod RPC port
  --db-host HOST         PostgreSQL host
  --rythmium-port PORT   Rythmium server port
  --log-level LEVEL      Logging level (DEBUG/INFO/WARNING/ERROR)
```

## Architecture

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

## Supported Rythmium Protocol Methods

| Method | Description |
|--------|-------------|
| `server.version` | Protocol negotiation |
| `server.ping` | Connection test |
| `server.banner` | Server banner |
| `blockchain.headers.subscribe` | Subscribe to new blocks |
| `blockchain.block.header` | Get block header |
| `blockchain.block.headers` | Get header range |
| `blockchain.scripthash.subscribe` | Subscribe to address |
| `blockchain.scripthash.get_balance` | Get address balance |
| `blockchain.scripthash.get_history` | Get transaction history |
| `blockchain.scripthash.get_mempool` | Get unconfirmed txs |
| `blockchain.scripthash.listunspent` | Get UTXOs |
| `blockchain.transaction.get` | Get transaction |
| `blockchain.transaction.broadcast` | Broadcast transaction |
| `mempool.get_fee_histogram` | Fee estimation |

## Development

### Setup

```bash
# Clone and install dev dependencies
git clone https://github.com/takumichronen/rythmicd_server.git
cd rythmicd_server
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# With coverage
pytest --cov=rythmicd

# Specific test file
pytest tests/test_scripthash.py
```

### Code Quality

```bash
# Format
black rythmicd tests

# Lint
ruff check rythmicd tests

# Type check
mypy rythmicd
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and data flow
- [Rythmium Protocol](docs/RYTHMIUM_PROTOCOL.md) - API reference
- [Deployment](docs/DEPLOYMENT.md) - Production deployment guide

## Miqrochain Specifications

| Parameter | Value |
|-----------|-------|
| Block time | 480 seconds (8 minutes) |
| Max supply | 26,280,000 MIQ |
| Halving interval | 262,800 blocks |
| Initial reward | 50 MIQ |
| Coinbase maturity | 100 blocks |
| Address prefix | Version byte 0x35 |
| RPC port | 9834 |

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Related Projects

- [Miqrochain](https://github.com/takumichronen/miqrochain) - Full node implementation
- [Rythmium](https://github.com/takumichronen/rythmium) - Android wallet

## Support

For issues and feature requests, please use the [GitHub issue tracker](https://github.com/takumichronen/rythmicd_server/issues).
