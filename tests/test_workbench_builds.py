"""Regression checks for CI distribution and frontend guards."""

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_builds", Path(__file__).resolve().parents[1] / "workbench/scripts/verify_builds.py"
)
assert SPEC and SPEC.loader
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


@pytest.mark.parametrize("change", ["none", "missing", "stale", "extra", "sdist"])
def test_distribution_resources(tmp_path, change):
    resources = tmp_path / "src/governance/workbench/resources"
    data = {
        "frontend/index.html": b"index",
        "frontend/assets/app.js": b"current",
        "demo/graph.json": b"{}",
    }
    for name, content in data.items():
        path = resources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    wheel_data = dict(data)
    if change == "missing":
        del wheel_data["frontend/index.html"]
    elif change == "stale":
        wheel_data["frontend/assets/app.js"] = b"old"
    elif change == "extra":
        wheel_data["frontend/assets/old.js"] = b"old"
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "test.whl", "w") as archive:
        for name, content in wheel_data.items():
            archive.writestr("governance/workbench/resources/" + name, content)
    with tarfile.open(dist / "test.tar.gz", "w:gz") as archive:
        for name, content in data.items():
            if change == "sdist":
                content = b"stale"
            info = tarfile.TarInfo("test/src/governance/workbench/resources/" + name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    if change == "none":
        CHECK.verify_distributions(dist, tmp_path)
    else:
        with pytest.raises(AssertionError):
            CHECK.verify_distributions(dist, tmp_path)


@pytest.mark.parametrize("change", ["none", "api", "remote", "scenario", "extra"])
def test_public_build(tmp_path, change):
    import shutil

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "workbench/public-demo", tmp_path / "workbench/public-demo")
    built = tmp_path / "workbench/dist-public"
    shutil.copytree(tmp_path / "workbench/public-demo", built)
    (built / "assets").mkdir()
    (built / "assets/app.js").write_text(
        "fetch('/api/workspace')" if change == "api" else "offline"
    )
    (built / "index.html").write_text(
        '<script src="https://example.com/app.js"></script>'
        if change == "remote"
        else '<script src="./assets/app.js"></script>'
    )
    if change == "scenario":
        (built / "scenarios/orders-impact/result.json").write_text("{}")
    if change == "extra":
        (built / ".env").write_text("unexpected")
    if change == "none":
        CHECK.verify_public(tmp_path)
    else:
        with pytest.raises(AssertionError):
            CHECK.verify_public(tmp_path)


@pytest.mark.parametrize(
    "body", ["", "<testcase><skipped/></testcase>", "<testcase><failure/></testcase>"]
)
def test_no_empty_skipped_or_failed_suite(tmp_path, body):
    path = tmp_path / "tests.xml"
    path.write_text("<testsuites><testsuite>" + body + "</testsuite></testsuites>")
    with pytest.raises(AssertionError):
        CHECK.verify_junit(path)


def test_executed_suite(tmp_path):
    path = tmp_path / "tests.xml"
    path.write_text("<testsuite><testcase/></testsuite>")
    CHECK.verify_junit(path)
