"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_POSTGRES_DB = "governance_demo"
DEFAULT_POSTGRES_USER = "postgres"
DEFAULT_POSTGRES_PASSWORD = "postgres"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_POSTGRES_SOURCE_NAME = "governance-demo"
DEFAULT_INVENTORY_OUTPUT_PATH = "artifacts/metadata-inventory.json"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for local development and demos."""

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_source_name: str
    inventory_output_path: str
    log_level: str = DEFAULT_LOG_LEVEL

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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def redacted(self) -> dict[str, str | int]:
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
        }


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
    values: dict[str, str | int] = {
        "postgres_host": env.get("POSTGRES_HOST", DEFAULT_POSTGRES_HOST),
        "postgres_port": int(env.get("POSTGRES_PORT", str(DEFAULT_POSTGRES_PORT))),
        "postgres_db": env.get("POSTGRES_DB", DEFAULT_POSTGRES_DB),
        "postgres_user": env.get("POSTGRES_USER", DEFAULT_POSTGRES_USER),
        "postgres_password": env.get("POSTGRES_PASSWORD", DEFAULT_POSTGRES_PASSWORD),
        "postgres_source_name": env.get("POSTGRES_SOURCE_NAME", DEFAULT_POSTGRES_SOURCE_NAME),
        "inventory_output_path": env.get("INVENTORY_OUTPUT_PATH", DEFAULT_INVENTORY_OUTPUT_PATH),
        "log_level": env.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
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
    )
