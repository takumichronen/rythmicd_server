"""
Application settings management.

Loads configuration from environment variables and/or YAML config files.
Supports miqrod RPC connection, PostgreSQL database, and Rythmium server settings.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class RPCConfig:
    """Miqrod RPC connection settings."""

    host: str = "127.0.0.1"
    port: int = 9834
    username: str | None = None
    password: str | None = None
    cookie_path: str | None = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0

    @property
    def url(self) -> str:
        """Get the RPC endpoint URL."""
        return f"http://{self.host}:{self.port}"


@dataclass
class DatabaseConfig:
    """PostgreSQL database settings."""

    host: str = "127.0.0.1"
    port: int = 5432
    database: str = "rythmicd"
    username: str = "rythmicd"
    password: str = ""
    min_connections: int = 5
    max_connections: int = 20
    command_timeout: float = 60.0

    @property
    def dsn(self) -> str:
        """Get the PostgreSQL connection string."""
        return (
            f"postgresql://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass
class RythmiumServerConfig:
    """Rythmium protocol server settings."""

    host: str = "0.0.0.0"
    port: int = 50001
    ssl_port: int | None = 50002
    ssl_cert_path: str | None = None
    ssl_key_path: str | None = None
    max_connections: int = 1000
    max_subscriptions_per_client: int = 10000
    max_message_size: int = 1_000_000  # 1MB
    client_timeout: float = 600.0  # 10 minutes
    banner: str = "Rythmicd - Miqrochain Rythmium Server"
    donation_address: str | None = None
    protocol_min: str = "1.4"
    protocol_max: str = "1.4.2"


@dataclass
class IndexerConfig:
    """Blockchain indexer settings."""

    poll_interval: float = 1.0  # seconds between chain tip checks
    mempool_poll_interval: float = 5.0  # seconds between mempool updates
    batch_size: int = 100  # blocks to process per batch during initial sync
    flush_interval: int = 1000  # blocks between db flushes during sync
    reorg_limit: int = 100  # max reorg depth to handle


@dataclass
class LoggingConfig:
    """Logging settings."""

    level: str = "INFO"
    json_format: bool = False
    module_levels: dict[str, str] = field(default_factory=dict)


@dataclass
class Settings:
    """
    Main application settings.

    Aggregates all configuration sections and provides loading from
    environment variables and YAML config files.
    """

    rpc: RPCConfig = field(default_factory=RPCConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    rythmium: RythmiumServerConfig = field(default_factory=RythmiumServerConfig)
    indexer: IndexerConfig = field(default_factory=IndexerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Chain-specific constants for Miqrochain
    chain_name: str = "miqrochain"
    network: str = "mainnet"  # mainnet, testnet, regtest
    address_version: int = 0x35  # P2PKH version byte


def _get_env(key: str, default: Any = None, cast: type | None = None) -> Any:
    """Get environment variable with optional type casting."""
    value = os.getenv(key, default)
    if value is None:
        return default
    if cast is not None and value is not None:
        if cast == bool:
            return str(value).lower() in ("true", "1", "yes")
        return cast(value)
    return value


def _load_yaml_config(path: Path) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two configuration dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> Settings:
    """
    Load settings from environment variables and optional config file.

    Priority (highest to lowest):
    1. Environment variables
    2. Config file (YAML)
    3. Default values

    Environment variables use the format:
    - RYTHMICD_RPC_HOST, RYTHMICD_RPC_PORT, etc.
    - RYTHMICD_DB_HOST, RYTHMICD_DB_PORT, etc.
    - RYTHMICD_RYTHMIUM_HOST, RYTHMICD_RYTHMIUM_PORT, etc.

    Args:
        config_path: Path to YAML configuration file
        env_file: Path to .env file (defaults to .env in current directory)

    Returns:
        Configured Settings instance
    """
    # Load .env file if present
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    # Load YAML config if provided
    yaml_config: dict[str, Any] = {}
    if config_path:
        yaml_config = _load_yaml_config(Path(config_path))

    # Build RPC config
    rpc_yaml = yaml_config.get("rpc", {})
    rpc = RPCConfig(
        host=_get_env("RYTHMICD_RPC_HOST", rpc_yaml.get("host", "127.0.0.1")),
        port=_get_env("RYTHMICD_RPC_PORT", rpc_yaml.get("port", 9834), int),
        username=_get_env("RYTHMICD_RPC_USERNAME", rpc_yaml.get("username")),
        password=_get_env("RYTHMICD_RPC_PASSWORD", rpc_yaml.get("password")),
        cookie_path=_get_env("RYTHMICD_RPC_COOKIE_PATH", rpc_yaml.get("cookie_path")),
        timeout=_get_env("RYTHMICD_RPC_TIMEOUT", rpc_yaml.get("timeout", 30.0), float),
        max_retries=_get_env("RYTHMICD_RPC_MAX_RETRIES", rpc_yaml.get("max_retries", 3), int),
        retry_delay=_get_env("RYTHMICD_RPC_RETRY_DELAY", rpc_yaml.get("retry_delay", 1.0), float),
    )

    # Build Database config
    db_yaml = yaml_config.get("database", {})
    database = DatabaseConfig(
        host=_get_env("RYTHMICD_DB_HOST", db_yaml.get("host", "127.0.0.1")),
        port=_get_env("RYTHMICD_DB_PORT", db_yaml.get("port", 5432), int),
        database=_get_env("RYTHMICD_DB_NAME", db_yaml.get("database", "rythmicd")),
        username=_get_env("RYTHMICD_DB_USERNAME", db_yaml.get("username", "rythmicd")),
        password=_get_env("RYTHMICD_DB_PASSWORD", db_yaml.get("password", "")),
        min_connections=_get_env(
            "RYTHMICD_DB_MIN_CONNECTIONS", db_yaml.get("min_connections", 5), int
        ),
        max_connections=_get_env(
            "RYTHMICD_DB_MAX_CONNECTIONS", db_yaml.get("max_connections", 20), int
        ),
        command_timeout=_get_env(
            "RYTHMICD_DB_COMMAND_TIMEOUT", db_yaml.get("command_timeout", 60.0), float
        ),
    )

    # Build Rythmium server config
    rythmium_yaml = yaml_config.get("rythmium", {})
    rythmium = RythmiumServerConfig(
        host=_get_env("RYTHMICD_RYTHMIUM_HOST", rythmium_yaml.get("host", "0.0.0.0")),
        port=_get_env("RYTHMICD_RYTHMIUM_PORT", rythmium_yaml.get("port", 50001), int),
        ssl_port=_get_env("RYTHMICD_RYTHMIUM_SSL_PORT", rythmium_yaml.get("ssl_port"), int),
        ssl_cert_path=_get_env(
            "RYTHMICD_RYTHMIUM_SSL_CERT", rythmium_yaml.get("ssl_cert_path")
        ),
        ssl_key_path=_get_env("RYTHMICD_RYTHMIUM_SSL_KEY", rythmium_yaml.get("ssl_key_path")),
        max_connections=_get_env(
            "RYTHMICD_RYTHMIUM_MAX_CONNECTIONS", rythmium_yaml.get("max_connections", 1000), int
        ),
        max_subscriptions_per_client=_get_env(
            "RYTHMICD_RYTHMIUM_MAX_SUBS",
            rythmium_yaml.get("max_subscriptions_per_client", 10000),
            int,
        ),
        max_message_size=_get_env(
            "RYTHMICD_RYTHMIUM_MAX_MSG_SIZE",
            rythmium_yaml.get("max_message_size", 1_000_000),
            int,
        ),
        client_timeout=_get_env(
            "RYTHMICD_RYTHMIUM_CLIENT_TIMEOUT",
            rythmium_yaml.get("client_timeout", 600.0),
            float,
        ),
        banner=_get_env(
            "RYTHMICD_RYTHMIUM_BANNER",
            rythmium_yaml.get("banner", "Rythmicd - Miqrochain Rythmium Server"),
        ),
        donation_address=_get_env(
            "RYTHMICD_RYTHMIUM_DONATION", rythmium_yaml.get("donation_address")
        ),
        protocol_min=_get_env(
            "RYTHMICD_RYTHMIUM_PROTOCOL_MIN", rythmium_yaml.get("protocol_min", "1.4")
        ),
        protocol_max=_get_env(
            "RYTHMICD_RYTHMIUM_PROTOCOL_MAX", rythmium_yaml.get("protocol_max", "1.4.2")
        ),
    )

    # Build Indexer config
    indexer_yaml = yaml_config.get("indexer", {})
    indexer = IndexerConfig(
        poll_interval=_get_env(
            "RYTHMICD_INDEXER_POLL_INTERVAL", indexer_yaml.get("poll_interval", 1.0), float
        ),
        mempool_poll_interval=_get_env(
            "RYTHMICD_INDEXER_MEMPOOL_INTERVAL",
            indexer_yaml.get("mempool_poll_interval", 5.0),
            float,
        ),
        batch_size=_get_env(
            "RYTHMICD_INDEXER_BATCH_SIZE", indexer_yaml.get("batch_size", 100), int
        ),
        flush_interval=_get_env(
            "RYTHMICD_INDEXER_FLUSH_INTERVAL", indexer_yaml.get("flush_interval", 1000), int
        ),
        reorg_limit=_get_env(
            "RYTHMICD_INDEXER_REORG_LIMIT", indexer_yaml.get("reorg_limit", 100), int
        ),
    )

    # Build Logging config
    logging_yaml = yaml_config.get("logging", {})
    logging_config = LoggingConfig(
        level=_get_env("RYTHMICD_LOG_LEVEL", logging_yaml.get("level", "INFO")),
        json_format=_get_env(
            "RYTHMICD_LOG_JSON", logging_yaml.get("json_format", False), bool
        ),
        module_levels=logging_yaml.get("module_levels", {}),
    )

    # Build main settings
    return Settings(
        rpc=rpc,
        database=database,
        rythmium=rythmium,
        indexer=indexer,
        logging=logging_config,
        chain_name=_get_env("RYTHMICD_CHAIN_NAME", yaml_config.get("chain_name", "miqrochain")),
        network=_get_env("RYTHMICD_NETWORK", yaml_config.get("network", "mainnet")),
        address_version=_get_env(
            "RYTHMICD_ADDRESS_VERSION", yaml_config.get("address_version", 0x35), int
        ),
    )
