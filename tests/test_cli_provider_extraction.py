"""CLI help/version must not require provider discovery (#98)."""

from __future__ import annotations

import pytest

from governance import __version__
from governance.cli import main


def test_main_help_and_version_succeed_without_providers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _forbidden(**_: object) -> tuple[object, ...]:
        raise AssertionError("discover_provider_registrations must not run for help/version")

    monkeypatch.setattr(
        "governance.providers.discovery.discover_provider_registrations",
        _forbidden,
    )
    monkeypatch.setattr(
        "governance.orchestration.registry.discover_provider_registrations",
        _forbidden,
    )

    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "governance" in out.lower()

    capsys.readouterr()
    assert main(["--version"]) == 0
    version_out = capsys.readouterr().out
    assert __version__ in version_out
