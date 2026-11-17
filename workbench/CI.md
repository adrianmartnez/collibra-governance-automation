# Workbench CI checks

CI retains the eight existing jobs and adds workbench-frontend. Use Python 3.12+
and Node.js 24. From the repository root, activate a virtual environment:

```text
python -m pip install -e ".[dev,workbench]"
npm ci --prefix workbench
npm run lint --prefix workbench
npm test --prefix workbench
npm run build --prefix workbench
npm run build:public --prefix workbench
python sample/public-demo/generate.py --check
python workbench/scripts/verify_builds.py --public
python -m pytest tests/test_workbench_api.py tests/test_public_demo.py tests/test_workbench_builds.py --junitxml=workbench-tests.xml
python workbench/scripts/verify_builds.py --junit workbench-tests.xml
ruff check workbench/scripts/verify_builds.py tests/test_workbench_builds.py
ruff format --check workbench/scripts/verify_builds.py tests/test_workbench_builds.py
```

Both build scripts run TypeScript (tsc -b) before Vite. Scenario verification
regenerates all three authorized scenarios using the existing engine and checks
every canonical byte. Public-build verification checks copied scenarios, local
HTML resources and absence of local API paths in compiled assets. Runtime tests
also check scenario integrity and offline loading. No running FastAPI server,
PostgreSQL, Collibra or credentials are needed. Dependency installation and
isolated Python build tooling may need network access.

The historical unit job now installs the workbench extra. The dedicated job also
imports its dependencies explicitly and rejects empty or skipped Python suites.

For distribution checks, first run the local frontend build above, then use an
empty output directory (choose a new path if this one contains artifacts):

```text
python -m build --outdir artifacts/workbench-distributions
python workbench/scripts/verify_builds.py --distributions artifacts/workbench-distributions
```

The package-validation job rebuilds the local frontend before its existing
wheel/sdist construction and clean-install smoke tests. The new archive guard
compares the complete frontend/demo resource inventory and bytes against that
fresh build in both archives, rejecting missing, extra or stale files.
