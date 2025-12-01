# Rythmicd Deployment Guide

## Prerequisites

- **Miqrod**: Running miqrod full node with RPC enabled
- **PostgreSQL**: Version 12 or higher
- **Python**: 3.11 or higher (for native installation)
- **Docker**: For containerized deployment (recommended)

## Quick Start with Docker

### 1. Clone and Configure

```bash
# Clone repository
git clone https://github.com/takumichronen/rythmicd_server.git
cd rythmicd_server

# Copy example config
cp config.yaml.example config.yaml

# Edit config (set your miqrod RPC credentials)
nano config.yaml
```

### 2. Configure Environment

Create a `.env` file:

```bash
# PostgreSQL
POSTGRES_PASSWORD=your_secure_password

# Miqrod RPC
MIQROD_HOST=host.docker.internal  # Or IP of miqrod server
MIQROD_RPC_PORT=9834
MIQROD_RPC_USER=
MIQROD_RPC_PASS=

# Rythmium ports
RYTHMIUM_TCP_PORT=50001
RYTHMIUM_SSL_PORT=50002

# Logging
LOG_LEVEL=INFO
```

### 3. Start Services

```bash
# Build and start
docker compose up -d

# View logs
docker compose logs -f rythmicd

# Check status
docker compose ps
```

### 4. Verify Operation

```bash
# Test Rythmium connection
echo '{"jsonrpc":"2.0","id":1,"method":"server.version","params":["test","1.4"]}' | nc localhost 50001
```

## Native Installation

### 1. Install Dependencies

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip postgresql postgresql-contrib

# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2. Create Database

```bash
# Create user and database
sudo -u postgres psql <<EOF
CREATE USER rythmicd WITH PASSWORD 'your_password';
CREATE DATABASE rythmicd OWNER rythmicd;
GRANT ALL PRIVILEGES ON DATABASE rythmicd TO rythmicd;
EOF
```

### 3. Install Rythmicd

```bash
# Clone repository
git clone https://github.com/takumichronen/rythmicd_server.git
cd rythmicd_server

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install
pip install -e .
```

### 4. Configure

```bash
# Copy config
cp config.yaml.example config.yaml

# Edit config
nano config.yaml
```

Or use environment variables:

```bash
export RYTHMICD_RPC_HOST=127.0.0.1
export RYTHMICD_RPC_PORT=9834
export RYTHMICD_DB_HOST=127.0.0.1
export RYTHMICD_DB_PASSWORD=your_password
export RYTHMICD_RYTHMIUM_PORT=50001
```

### 5. Run

```bash
# With config file
rythmicd --config config.yaml

# Or with environment variables
rythmicd --log-level INFO
```

## Miqrod Configuration

Ensure miqrod is configured to accept RPC connections:

```bash
# In miqrod config or command line:
--rpc-bind=127.0.0.1:9834
```

For cookie authentication (default), rythmicd will read the cookie file from the miqrod data directory.

For user/password authentication:

```yaml
# config.yaml
rpc:
  username: "rpcuser"
  password: "rpcpassword"
```

## TLS/SSL Configuration

### Option 1: Direct SSL (in rythmicd)

Generate certificates:

```bash
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key -out certs/server.crt -days 365 -nodes
```

Configure:

```yaml
rythmium:
  ssl_port: 50002
  ssl_cert_path: "/path/to/certs/server.crt"
  ssl_key_path: "/path/to/certs/server.key"
```

### Option 2: Reverse Proxy (recommended for production)

Using nginx:

```nginx
stream {
    upstream rythmicd {
        server 127.0.0.1:50001;
    }

    server {
        listen 50002 ssl;
        proxy_pass rythmicd;

        ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
    }
}
```

## Systemd Service

Create `/etc/systemd/system/rythmicd.service`:

```ini
[Unit]
Description=Rythmicd - Miqrochain Rythmium Server
After=network.target postgresql.service

[Service]
Type=simple
User=rythmicd
Group=rythmicd
WorkingDirectory=/opt/rythmicd
ExecStart=/opt/rythmicd/venv/bin/rythmicd --config /etc/rythmicd/config.yaml
Restart=always
RestartSec=10

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/rythmicd

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rythmicd
sudo systemctl start rythmicd
```

## Monitoring

### Logs

```bash
# Docker
docker compose logs -f rythmicd

# Systemd
journalctl -u rythmicd -f

# JSON logs for log aggregation
RYTHMICD_LOG_JSON=true rythmicd
```

### Health Check

The server responds to `server.ping`:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"server.ping","params":[]}' | nc localhost 50001
```

### Prometheus Metrics (future)

Metrics endpoint can be added for Prometheus scraping.

## Performance Tuning

### PostgreSQL

```sql
-- Recommended settings for rythmicd workload
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '128MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET random_page_cost = 1.1;
SELECT pg_reload_conf();
```

### Connection Pool

```yaml
database:
  min_connections: 10
  max_connections: 50
```

### Indexer

For initial sync:

```yaml
indexer:
  batch_size: 500      # More blocks per batch
  flush_interval: 5000  # Less frequent flushes
```

For production:

```yaml
indexer:
  batch_size: 100
  poll_interval: 1.0
  mempool_poll_interval: 5.0
```

## Troubleshooting

### Connection Refused to miqrod

1. Check miqrod is running: `miqrod --version`
2. Check RPC is enabled and bound correctly
3. Check firewall allows connection
4. Verify credentials

### Database Connection Failed

1. Check PostgreSQL is running: `systemctl status postgresql`
2. Verify database exists: `psql -U rythmicd -d rythmicd -c '\dt'`
3. Check connection string in config

### Sync is Slow

1. Increase `batch_size` in indexer config
2. Check PostgreSQL performance
3. Ensure miqrod is fully synced
4. Check disk I/O

### High Memory Usage

1. Reduce `header_cache_size`
2. Reduce database connection pool
3. Monitor for memory leaks with profiling

## Backup and Recovery

### Database Backup

```bash
# Backup
pg_dump -U rythmicd rythmicd > rythmicd_backup.sql

# Restore
psql -U rythmicd rythmicd < rythmicd_backup.sql
```

### Reindexing

If the database becomes corrupted, you can reindex:

```bash
# Drop and recreate database
sudo -u postgres psql -c "DROP DATABASE rythmicd;"
sudo -u postgres psql -c "CREATE DATABASE rythmicd OWNER rythmicd;"

# Restart rythmicd - it will sync from genesis
systemctl restart rythmicd
```
