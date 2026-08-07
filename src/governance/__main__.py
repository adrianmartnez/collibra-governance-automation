"""Minimal package entry point."""

from __future__ import annotations

from governance import __version__
from governance.config import load_settings


def main() -> int:
    settings = load_settings()
    redacted = settings.redacted()
    print(f"governance {__version__}")
    print(
        "postgres="
        f"{redacted['postgres_user']}@{redacted['postgres_host']}:"
        f"{redacted['postgres_port']}/{redacted['postgres_db']} "
        f"password={redacted['postgres_password']} "
        f"log_level={redacted['log_level']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
