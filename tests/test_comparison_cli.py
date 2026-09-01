"""CLI tests for governance compare."""

from __future__ import annotations

import json
from pathlib import Path

from conftest_comparison import build_snapshot
from governance.cli import main
from governance.comparison import canonical_comparison_json
from governance.snapshots import write_snapshot


def test_compare_cli_identical_human(tmp_path: Path, capsys) -> None:
    snap = build_snapshot()
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_snapshot(snap, a)
    write_snapshot(snap, b)
    code = main(["compare", "--baseline", str(a), "--candidate", str(b)])
    assert code == 0
    out = capsys.readouterr().out
    assert "status=identical" in out
    assert "writes=0" in out


def test_compare_cli_json_and_output(tmp_path: Path, capsys) -> None:
    snap = build_snapshot()
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    out_path = tmp_path / "cmp.json"
    write_snapshot(snap, a)
    write_snapshot(snap, b)
    code = main(
        [
            "compare",
            "--baseline",
            str(a),
            "--candidate",
            str(b),
            "--format",
            "json",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    stdout = capsys.readouterr().out
    artifact = out_path.read_text(encoding="utf-8")
    assert stdout == artifact
    payload = json.loads(artifact)
    assert payload["status"] == "identical"
    assert payload["writes_performed"] == 0
    assert canonical_comparison_json(payload) == artifact


def test_compare_cli_different_exit_zero(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_snapshot(build_snapshot(description=None), a)
    write_snapshot(build_snapshot(description="x"), b)
    assert main(["compare", "--baseline", str(a), "--candidate", str(b)]) == 0


def test_compare_cli_alignment_required_exit_4(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_snapshot(build_snapshot(source_name="dev"), a)
    write_snapshot(build_snapshot(source_name="prod"), b)
    assert main(["compare", "--baseline", str(a), "--candidate", str(b)]) == 4


def test_compare_cli_failure_does_not_write_output(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    out_path = tmp_path / "cmp.json"
    write_snapshot(build_snapshot(), a)
    missing = tmp_path / "missing.json"
    code = main(
        [
            "compare",
            "--baseline",
            str(a),
            "--candidate",
            str(missing),
            "--output",
            str(out_path),
        ]
    )
    assert code == 4
    assert not out_path.exists()


def test_compare_cli_usage_missing_baseline() -> None:
    # argparse SystemExit → main maps to non-zero; typically 2
    code = main(["compare", "--candidate", "x.json"])
    assert code == 2
