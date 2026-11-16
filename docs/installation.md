# Install and verify local distributions

Python 3.12 or newer is required. These instructions build local artifacts;
they do not publish to PyPI or TestPyPI and make no publication claim. The
package version remains 2.0.0 while the next release is being prepared.

## Build wheel and sdist

Use a dedicated build virtual environment, never the system Python environment:

```sh
python3.12 -m venv .build-venv
. .build-venv/bin/activate
python -m pip install build
python -m build --outdir dist
```

On Windows, use `py -3.12 -m venv .build-venv` and activate with
`.build-venv\Scripts\Activate.ps1` instead. The build frontend creates isolated
backend environments using the requirements in `pyproject.toml`. Setuptools
77.0.3+ is needed for the existing SPDX license declaration. The default build
creates the sdist first and builds the wheel from that sdist.

Expected artifacts (current version):

- `collibra_governance_automation-2.0.0-py3-none-any.whl`
- `collibra_governance_automation-2.0.0.tar.gz`

Both contain all runtime modules, JSON schemas and ODCS license/notice files.
The sdist also includes `sample/offline-impact/` and this installation guide.
The demo is a companion example, not an installed executable or wheel resource.

Installing build tools and runtime dependencies may access a package index.
This is separate from the offline execution guarantee below. For fully offline
installation, first prepare a wheelhouse on a connected machine for the same
Python version/platform, including the declared runtime dependencies and build
tools (`setuptools>=77.0.3` and `wheel`). Then use `--no-index --find-links /path/to/wheelhouse`
for pip and set `PIP_NO_INDEX=1` and `PIP_FIND_LINKS` to the wheelhouse for isolated
build subprocesses. No extra provider packages are required.

## Install outside the checkout

Copy both distribution files to a working directory outside the source checkout
and change into that directory. Unset `PYTHONPATH` and `PYTHONHOME`. Extract the
sdist to obtain the self-contained demo inputs, for either installation:

```sh
python -m tarfile -e collibra_governance_automation-2.0.0.tar.gz unpacked
```

Create two independent virtual environments with Python 3.12+ (no
`--system-site-packages`). Activate only one at a time. On POSIX:

```sh
python3.12 -m venv wheel-env
. wheel-env/bin/activate
python -m pip install ./collibra_governance_automation-2.0.0-py3-none-any.whl
python -m pip check
governance --version
governance report --help
python -c "import governance; print(governance.__file__)"
```

The printed package path must be inside `wheel-env`, not the checkout. After
running the offline flow below, deactivate and independently check the sdist:

```sh
deactivate
python3.12 -m venv sdist-env
. sdist-env/bin/activate
python -m pip install --no-cache-dir ./collibra_governance_automation-2.0.0.tar.gz
python -m pip check
governance --version
governance report --help
python -c "import governance; print(governance.__file__)"
```

On Windows, create environments with `py -3.12 -m venv wheel-env` (or
`sdist-env`) and activate with `wheel-env\Scripts\Activate.ps1` (or the sdist
counterpart). In PowerShell, remove environment overrides with
`Remove-Item Env:PYTHONPATH, Env:PYTHONHOME -ErrorAction SilentlyContinue`.
All pip and governance commands above are otherwise the same. Do not install
with `-e`; the sdist installation must build its own wheel.

## Run the complete offline flow in each environment

After dependency installation, network access can be disabled. From the external
working directory, run the demo shipped inside the sdist:

```sh
python unpacked/collibra_governance_automation-2.0.0/sample/offline-impact/run.py
governance report --input offline-impact-result.json --output report.html
```

Both commands return 0. The demo generates a canonical
`governance-impact-result` v1 artifact and records `writes_performed: 0`.
The report command validates its schema and integrity before saving HTML.
Open `report.html` as a local file; it has no JavaScript or external assets.
Repeat the commands to obtain identical JSON and HTML bytes. Compare the JSON
with the demo's `expected-result.json`. The hypothetical table removal is only
an input scenario; no removal is executed.

No PostgreSQL process, Collibra tenant, configuration, `.env`, credentials or
external providers are needed. Missing lineage and policy coverage remain
unknown; the report does not establish production safety or compliance.

## Focused distribution verification

In the development environment with the declared dev extras available:

```sh
python -m pytest -q -s tests/test_report_wheel.py
python -m pytest -q tests/test_package.py tests/test_report_cli.py tests/test_impact_html.py
```

The existing distribution test covers wheel and sdist in separate clean venvs,
checks every runtime module/schema/notice against the built artifacts and the
installed resources, verifies the entrypoint and `pip check`, and runs the
sdist-shipped demo and deterministic HTML reporting outside the checkout with
network and runtime/provider resolution denied. It also runs the existing CLI
regressions against each installed package. Runtime dependencies are repacked
from the test host's already installed distributions for offline installation;
setup may download declared build tools. No package is published.
