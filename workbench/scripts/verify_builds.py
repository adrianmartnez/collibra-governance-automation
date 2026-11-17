"""Dependency-free CI checks for freshly built workbench distributions."""

from __future__ import annotations

import argparse
import json
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def files(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }


def verify_public(root: Path = ROOT) -> None:
    source = files(root / "workbench/public-demo")
    built = files(root / "workbench/dist-public")
    manifest = json.loads(source["scenarios/manifest.json"])
    assert {s["id"] for s in manifest["scenarios"]} == {
        "orders-impact",
        "isolated-inventory",
        "multi-branch-pipeline",
    }, "Expected all three authorized scenarios"
    for name, content in source.items():
        assert built.get(name) == content, f"Public scenario differs: {name}"
    assert built.get("index.html"), "Missing public index"
    assets = {name: data for name, data in built.items() if name.startswith("assets/")}
    assert assets and any(name.endswith(".js") for name in assets), "Missing public JavaScript"
    assert set(built) == set(source) | set(assets) | {"index.html"}, "Unexpected public files"
    for name, data in {"index.html": built["index.html"], **assets}.items():
        assert b"/api/" not in data, f"Local API dependency in {name}"
        assert b"http://localhost" not in data and b"http://127.0.0.1" not in data, (
            f"Local server dependency in {name}"
        )

    # Bundled third-party SVG namespace URLs are not network resources.
    class Resources(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"src", "href"} and value:
                    assert value.startswith("./"), f"Nonlocal public resource: {value}"
                    assert value[2:] in built, f"Missing public resource: {value}"

    Resources().feed(built["index.html"].decode())


def verify_distributions(directory: Path, root: Path = ROOT) -> None:
    expected = files(root / "src/governance/workbench/resources")
    assert "frontend/index.html" in expected and any(
        name.startswith("frontend/assets/") and name.endswith(".js") for name in expected
    ), "Build the frontend before packaging"
    assert any(name.startswith("demo/") for name in expected), "Missing packaged demo"
    wheels, sdists = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Expected exactly one wheel and one sdist"
    prefix = "governance/workbench/resources/"
    with zipfile.ZipFile(wheels[0]) as archive:
        actual = {
            name.removeprefix(prefix): archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    assert actual == expected, "Wheel resources differ from freshly built frontend/demo"
    with tarfile.open(sdists[0]) as archive:
        actual = {}
        for member in archive.getmembers():
            relative = member.name.partition("/")[2]
            if relative.startswith("src/" + prefix) and member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                actual[relative.removeprefix("src/" + prefix)] = stream.read()
    assert actual == expected, "Sdist resources differ from freshly built frontend/demo"


def verify_junit(path: Path) -> None:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    assert cases, "No workbench tests executed"
    assert not any(case.find("skipped") is not None for case in cases), "Workbench tests skipped"
    assert not any(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    ), "Workbench tests failed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--distributions", type=Path)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    if not (args.public or args.distributions or args.junit):
        parser.error("Select at least one verification")
    if args.public:
        verify_public()
    if args.distributions:
        verify_distributions(args.distributions)
    if args.junit:
        verify_junit(args.junit)
    print("Workbench build verification passed")


if __name__ == "__main__":
    main()
