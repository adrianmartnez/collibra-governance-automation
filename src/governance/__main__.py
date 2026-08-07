"""Package entry point delegating to the governance CLI."""

from __future__ import annotations

from governance.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
