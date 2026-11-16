"""Offline report CLI, input preservation and atomic output regression tests."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import psycopg
import pytest

from governance.cli import main
from governance.impact.html import render_impact_report_from_file

DEMO = Path(__file__).resolve().parents[1] / "sample/offline-impact/expected-result.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("report attempted runtime/provider/network access")

    for name in ("load_settings", "load_operation_runtime", "build_provider_registry"):
        monkeypatch.setattr(f"governance.cli.{name}", denied)
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr(psycopg, "connect", denied)
    monkeypatch.setattr(psycopg.Connection, "connect", denied)


def test_report_success_and_determinism(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_bytes(DEMO.read_bytes())
    original = source.read_bytes()
    output = tmp_path / "nested" / "report.html"
    args = ["report", "--input", str(source), "--output", str(output)]
    assert main(args) == 0
    first = output.read_bytes()
    assert first == render_impact_report_from_file(source).encode("utf-8")
    assert main(args) == 0
    assert output.read_bytes() == first
    assert source.read_bytes() == original
    assert "Impact identified" in first.decode()
    assert not list(output.parent.glob("*.tmp"))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "kind,diagnostic",
    [
        ("missing", "unable to read"),
        ("json", "invalid JSON"),
        ("version", "unsupported result_version"),
        ("integrity", "identity mismatch"),
        ("schema", "required property"),
        ("utf8", "invalid UTF-8"),
    ],
)
def test_invalid_input_never_creates_or_overwrites(tmp_path, capsys, existing, kind, diagnostic):
    source = tmp_path / "input.json"
    result = json.loads(DEMO.read_text())
    if kind == "version":
        result["result_version"] = "9"
    elif kind == "integrity":
        result["status"] = "clear"
    elif kind == "schema":
        del result["impact"]
    if kind != "missing":
        source.write_bytes(
            b"{" if kind == "json" else b"\xff" if kind == "utf8" else json.dumps(result).encode()
        )
    output = tmp_path / "report.html"
    if existing:
        output.write_bytes(b"previous report")
    assert main(["report", "--input", str(source), "--output", str(output)]) == 4
    captured = capsys.readouterr()
    assert diagnostic.lower() in captured.err.lower()
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert output.read_bytes() == b"previous report" if existing else not output.exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_failed_write_preserves_output_and_removes_temp(tmp_path, monkeypatch, capsys, operation):
    output = tmp_path / "report.html"
    output.write_bytes(b"previous report")

    def denied(*args):
        raise PermissionError("output is locked")

    monkeypatch.setattr(f"governance.io.atomic.os.{operation}", denied)
    assert main(["report", "--input", str(DEMO), "--output", str(output)]) == 1
    assert output.read_bytes() == b"previous report"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.html"]
    assert "unable to write HTML report" in capsys.readouterr().err


def test_directory_output_fails(tmp_path, capsys):
    assert main(["report", "--input", str(DEMO), "--output", str(tmp_path)]) == 1
    assert "directory" in capsys.readouterr().err.lower()


def test_same_input_output_is_rejected(tmp_path, capsys):
    source = tmp_path / "result.json"
    source.write_bytes(DEMO.read_bytes())
    original = source.read_bytes()
    assert main(["report", "--input", str(source), "--output", str(source)]) == 2
    assert source.read_bytes() == original
    assert "different files" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args", [["report"], ["report", "--input", "x"], ["report", "--output", "x"]]
)
def test_required_arguments(args, capsys):
    assert main(args) == 2
    assert "required" in capsys.readouterr().err


def test_help(capsys):
    assert main(["report", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--input" in output and "--output" in output


def test_hardlink_to_input_is_rejected(tmp_path, capsys):
    source = tmp_path / "result.json"
    source.write_bytes(DEMO.read_bytes())
    output = tmp_path / "alias.html"
    output.hardlink_to(source)
    original = source.read_bytes()
    assert main(["report", "--input", str(source), "--output", str(output)]) == 2
    assert source.read_bytes() == output.read_bytes() == original
    assert "different files" in capsys.readouterr().err
