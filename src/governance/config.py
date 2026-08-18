"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

from dotenv import load_dotenv

DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_POSTGRES_DB = "governance_demo"
DEFAULT_POSTGRES_USER = "postgres"
DEFAULT_POSTGRES_PASSWORD = "postgres"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_POSTGRES_SOURCE_NAME = "governance-demo"
DEFAULT_INVENTORY_OUTPUT_PATH = "artifacts/metadata-inventory.json"
DEFAULT_COLLIBRA_MODE = "mock"
DEFAULT_COLLIBRA_TIMEOUT_SECONDS = 10.0
DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS = 300.0
DEFAULT_COLLIBRA_EXECUTION_MODE = "core_rest"
ALLOWED_COLLIBRA_EXECUTION_MODES = frozenset({"core_rest", "import_v2", "sync_v2"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for local development and demos."""

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str = field(repr=False)
    postgres_source_name: str
    inventory_output_path: str
    log_level: str = DEFAULT_LOG_LEVEL
    collibra_mode: str = DEFAULT_COLLIBRA_MODE
    collibra_base_url: str = ""
    collibra_username: str = ""
    collibra_password: str = field(default="", repr=False)
    collibra_bearer_token: str = field(default="", repr=False)
    collibra_client_id: str = ""
    collibra_client_secret: str = field(default="", repr=False)
    collibra_token_url: str = ""
    collibra_oauth_scope: str = ""
    collibra_oauth_client_auth: str = ""
    collibra_timeout_seconds: float = DEFAULT_COLLIBRA_TIMEOUT_SECONDS
    collibra_job_poll_interval_seconds: float = DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS
    collibra_job_poll_timeout_seconds: float = DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS
    collibra_execution_mode: str = DEFAULT_COLLIBRA_EXECUTION_MODE
    collibra_synchronization_id: str = ""

    def __post_init__(self) -> None:
        if not self.postgres_host.strip():
            raise ValueError("postgres_host is required")
        if self.postgres_port <= 0:
            raise ValueError("postgres_port must be a positive integer")
        if not self.postgres_db.strip():
            raise ValueError("postgres_db is required")
        if not self.postgres_user.strip():
            raise ValueError("postgres_user is required")
        if not self.postgres_password:
            raise ValueError("postgres_password is required")
        if not self.postgres_source_name.strip():
            raise ValueError("postgres_source_name is required")
        if not self.inventory_output_path.strip():
            raise ValueError("inventory_output_path is required")
        if not self.log_level.strip():
            raise ValueError("log_level is required")
        mode = self.collibra_mode.strip().lower()
        if mode not in {"mock", "live"}:
            raise ValueError("collibra_mode must be 'mock' or 'live'")
        object.__setattr__(self, "collibra_mode", mode)
        if self.collibra_timeout_seconds <= 0:
            raise ValueError("collibra_timeout_seconds must be positive")
        if self.collibra_job_poll_interval_seconds <= 0:
            raise ValueError("collibra_job_poll_interval_seconds must be positive")
        if self.collibra_job_poll_timeout_seconds <= 0:
            raise ValueError("collibra_job_poll_timeout_seconds must be positive")
        if self.collibra_job_poll_timeout_seconds < self.collibra_job_poll_interval_seconds:
            raise ValueError(
                "collibra_job_poll_timeout_seconds must be >= collibra_job_poll_interval_seconds"
            )
        execution = self.collibra_execution_mode.strip().lower() or DEFAULT_COLLIBRA_EXECUTION_MODE
        if execution not in ALLOWED_COLLIBRA_EXECUTION_MODES:
            raise ValueError("collibra_execution_mode must be core_rest, import_v2, or sync_v2")
        object.__setattr__(self, "collibra_execution_mode", execution)
        sync_id = self.collibra_synchronization_id.strip()
        if sync_id:
            try:
                sync_id = str(UUID(sync_id))
            except ValueError as exc:
                raise ValueError("collibra_synchronization_id must be a UUID") from exc
        object.__setattr__(self, "collibra_synchronization_id", sync_id)

    def __repr__(self) -> str:
        body = ", ".join(f"{key}={value!r}" for key, value in self.redacted().items())
        return f"{type(self).__name__}({body})"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def redacted(self) -> dict[str, str | int | float]:
        """Return a logging-safe representation with secrets masked."""
        return {
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "postgres_password": "***",
            "postgres_source_name": self.postgres_source_name,
            "inventory_output_path": self.inventory_output_path,
            "log_level": self.log_level,
            "collibra_mode": self.collibra_mode,
            "collibra_base_url": _diagnostic_http_url(self.collibra_base_url),
            "collibra_username": self.collibra_username,
            "collibra_password": "***" if self.collibra_password else "",
            "collibra_bearer_token": "***" if self.collibra_bearer_token else "",
            "collibra_client_id": self.collibra_client_id,
            "collibra_client_secret": "***" if self.collibra_client_secret else "",
            "collibra_token_url": _diagnostic_http_url(self.collibra_token_url),
            "collibra_oauth_scope": self.collibra_oauth_scope,
            "collibra_oauth_client_auth": self.collibra_oauth_client_auth,
            "collibra_timeout_seconds": self.collibra_timeout_seconds,
            "collibra_job_poll_interval_seconds": self.collibra_job_poll_interval_seconds,
            "collibra_job_poll_timeout_seconds": self.collibra_job_poll_timeout_seconds,
            "collibra_execution_mode": self.collibra_execution_mode,
            "collibra_synchronization_id": self.collibra_synchronization_id,
        }


def _diagnostic_http_url(url: str) -> str:
    """Return scheme/host/path only. Never emit userinfo, query, or fragment."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return "<redacted-url>"
    hostname = parsed.hostname
    if not hostname:
        return "<redacted-url>"
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return f"{parsed.scheme}://{netloc}{parsed.path or ''}"


def _parse_database_url(url: str) -> dict[str, str | int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("DATABASE_URL must use postgresql:// scheme")
    if not parsed.hostname or not parsed.path or parsed.path == "/":
        raise ValueError("DATABASE_URL must include host and database name")
    port = parsed.port if parsed.port is not None else DEFAULT_POSTGRES_PORT
    return {
        "postgres_host": parsed.hostname,
        "postgres_port": port,
        "postgres_db": parsed.path.lstrip("/"),
        "postgres_user": parsed.username or DEFAULT_POSTGRES_USER,
        "postgres_password": parsed.password or DEFAULT_POSTGRES_PASSWORD,
    }


def load_settings(
    *, dotenv_path: str | None = ".env", environ: dict[str, str] | None = None
) -> Settings:
    """Load settings from environment, optionally reading a local .env file."""
    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)

    env = environ if environ is not None else os.environ
    values: dict[str, str | int | float] = {
        "postgres_host": env.get("POSTGRES_HOST", DEFAULT_POSTGRES_HOST),
        "postgres_port": int(env.get("POSTGRES_PORT", str(DEFAULT_POSTGRES_PORT))),
        "postgres_db": env.get("POSTGRES_DB", DEFAULT_POSTGRES_DB),
        "postgres_user": env.get("POSTGRES_USER", DEFAULT_POSTGRES_USER),
        "postgres_password": env.get("POSTGRES_PASSWORD", DEFAULT_POSTGRES_PASSWORD),
        "postgres_source_name": env.get("POSTGRES_SOURCE_NAME", DEFAULT_POSTGRES_SOURCE_NAME),
        "inventory_output_path": env.get("INVENTORY_OUTPUT_PATH", DEFAULT_INVENTORY_OUTPUT_PATH),
        "log_level": env.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        "collibra_mode": env.get("COLLIBRA_MODE", DEFAULT_COLLIBRA_MODE),
        "collibra_base_url": env.get("COLLIBRA_BASE_URL", ""),
        "collibra_username": env.get("COLLIBRA_USERNAME", ""),
        "collibra_password": env.get("COLLIBRA_PASSWORD", ""),
        "collibra_bearer_token": env.get("COLLIBRA_BEARER_TOKEN", ""),
        "collibra_client_id": env.get("COLLIBRA_CLIENT_ID", ""),
        "collibra_client_secret": env.get("COLLIBRA_CLIENT_SECRET", ""),
        "collibra_token_url": env.get("COLLIBRA_TOKEN_URL", ""),
        "collibra_oauth_scope": env.get("COLLIBRA_OAUTH_SCOPE", ""),
        "collibra_oauth_client_auth": env.get("COLLIBRA_OAUTH_CLIENT_AUTH", ""),
        "collibra_timeout_seconds": float(
            env.get("COLLIBRA_TIMEOUT_SECONDS", str(DEFAULT_COLLIBRA_TIMEOUT_SECONDS))
        ),
        "collibra_job_poll_interval_seconds": float(
            env.get(
                "COLLIBRA_JOB_POLL_INTERVAL_SECONDS",
                str(DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS),
            )
        ),
        "collibra_job_poll_timeout_seconds": float(
            env.get(
                "COLLIBRA_JOB_POLL_TIMEOUT_SECONDS",
                str(DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS),
            )
        ),
        "collibra_execution_mode": env.get(
            "COLLIBRA_EXECUTION_MODE", DEFAULT_COLLIBRA_EXECUTION_MODE
        ),
        "collibra_synchronization_id": env.get("COLLIBRA_SYNCHRONIZATION_ID", ""),
    }

    database_url = env.get("DATABASE_URL")
    if database_url:
        values.update(_parse_database_url(database_url))

    return Settings(
        postgres_host=str(values["postgres_host"]),
        postgres_port=int(values["postgres_port"]),
        postgres_db=str(values["postgres_db"]),
        postgres_user=str(values["postgres_user"]),
        postgres_password=str(values["postgres_password"]),
        postgres_source_name=str(values["postgres_source_name"]),
        inventory_output_path=str(values["inventory_output_path"]),
        log_level=str(values["log_level"]),
        collibra_mode=str(values["collibra_mode"]),
        collibra_base_url=str(values["collibra_base_url"]),
        collibra_username=str(values["collibra_username"]),
        collibra_password=str(values["collibra_password"]),
        collibra_bearer_token=str(values["collibra_bearer_token"]),
        collibra_client_id=str(values["collibra_client_id"]),
        collibra_client_secret=str(values["collibra_client_secret"]),
        collibra_token_url=str(values["collibra_token_url"]),
        collibra_oauth_scope=str(values["collibra_oauth_scope"]),
        collibra_oauth_client_auth=str(values["collibra_oauth_client_auth"]),
        collibra_timeout_seconds=float(values["collibra_timeout_seconds"]),
        collibra_job_poll_interval_seconds=float(values["collibra_job_poll_interval_seconds"]),
        collibra_job_poll_timeout_seconds=float(values["collibra_job_poll_timeout_seconds"]),
        collibra_execution_mode=str(values["collibra_execution_mode"]),
        collibra_synchronization_id=str(values["collibra_synchronization_id"]),
    )
