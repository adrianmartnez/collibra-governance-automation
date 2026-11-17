# Governance Control Center

An optional, local React + TypeScript + Vite workbench for the existing Python
governance engine. This is a local application distributed with precompiled web resources, not a hosted service
or a new machine contract. The core package version and Provider SDK API "1"
are unchanged. Web dependencies are only installed by the `workbench` extra.

## Start locally (PowerShell)

Requirements: Python 3.12+, Node 22.12+ (Node 24 recommended), npm, and this
checkout for frontend development. Production installations need only Python and the workbench extra. Setup can download dependencies;
using the app and synthetic demo after setup requires no external service.

From the repository root:

```powershell
py -3.12 -m venv .venv-workbench
.\.venv-workbench\Scripts\python.exe -m pip install -e ".[workbench,dev]"
npm --prefix workbench ci
npm --prefix workbench run build
.\.venv-workbench\Scripts\python.exe -m uvicorn governance.workbench.api:app --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765**. The Python server serves the compiled frontend
and API from the same origin. Stop it with Ctrl+C. No PostgreSQL, Docker,
Collibra tenant, `.env`, API keys or third-party providers are needed.

For frontend development, keep that backend running and start a second terminal:

```powershell
npm --prefix workbench run dev
```

Open **http://127.0.0.1:5173**. Vite binds only to loopback and proxies `/api`
to port 8765. Rebuild the frontend before reviewing changes on port 8765.
On POSIX, create the venv with `python3.12 -m venv .venv-workbench` and substitute
`.venv-workbench/bin/python` for the Windows executable. The npm commands are
identical. Use the documented hosts and ports; the API rejects other Host and
Origin values. Never expose either server to an untrusted network.

## Install a built distribution (no Node or checkout at runtime)

The wheel and sdist include precompiled HTML/JavaScript/CSS and the synthetic demo.
From a directory outside the checkout, using the actual local distribution path:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "C:\path\to\collibra_governance_automation-2.0.0-py3-none-any.whl[workbench]"
.\.venv\Scripts\python.exe -m uvicorn governance.workbench.api:app --host 127.0.0.1 --port 8765
```

The same installation command accepts the generated `.tar.gz` sdist. No PyPI
publication is implied. Neither installation is editable; no PYTHONPATH is needed.
Runtime resources are resolved with importlib.resources, independently of cwd.

For source maintenance, `npm --prefix workbench run build` regenerates the packaged
frontend in `src/governance/workbench/resources/frontend/`. Include those precompiled
resources with source distributions; Python builds do not invoke npm or download
visual assets. The two demo JSON resources are canonical copies of sample fixtures,
guarded by a focused equality test. Existing Python build/publication procedures
are unchanged. Keep the loopback binding; this API is not designed for public hosting.

## Verified workflow

1. Open **Impact Analysis** and press **Load demo**. The backend reads the existing
   synthetic dbt manifest, selects `orders` from the changes fixture and calls the
   existing impact engine. Nothing is hardcoded in React.
2. The **Graph nodes** indicator has **6 identities**: a data source, a dataset and four tables.
   The sidebar initially shows **Tables: 4**, counting only the four table models. Search/classification filters further narrow that list without changing the full graph-node total. The result has **1 changed
   root**, **1 direct impact** (`order_totals`) and **1 transitive impact**
   (`revenue_report`). `inventory` has no recorded dependency edges and is not
   reached. No policies are supplied; a dash is not a fabricated policy count.
3. Select nodes in the sidebar or graph. Pan, zoom, fit the graph, inspect
   relationships and open **Impact paths** for exact recorded steps/provenance.
   The graph shows dependency-to-consumer arrows for `depends_on`, the reverse
   of its stored direction. Relationship tables explicitly retain stored direction.
4. **Analyze selected** runs the real engine with that identity as the explicit
   hypothetical change. Selecting `inventory` produces `clear` in the supplied
   graph. Selecting `order_totals` produces one direct and no transitive impacts.
   This neither detects schema changes automatically nor executes a removal.
5. **Export HTML report** posts the validated impact result to the existing
   Python renderer and downloads a self-contained HTML document. It is not
   generated from React's view data.
6. **Dashboard**, **Lineage**, **Impact Analysis**, **Policies** and **Plans** are
   real screens; the current in-memory selection survives navigation. Policies
   are matches, not automatically violations. A separate review artifact can
   supply recorded conflicts and reconciliation assessments; it is clearly not
   auto-associated with an unrelated impact graph.
7. **Plans → Open saved plan** accepts existing `.gplan` v1/v2 documents and uses
   the existing plan loader's schema, integrity and action validation. Actions,
   target context, reasons, recorded decisions and assumptions are inspectable.
   No apply/sync/execute operation exists. Freshness and current remote safety
   are not checked. Artifact integrity is not authorization to execute a plan.

## Bring your own evidence

**Import artifact** accepts local JSON contents, not server paths or URLs:

- **dbt manifest v12** plus an explicit logical namespace: full supplied graph;
  select an identity to analyze. Policies are not newly evaluated by this route.
- **governance-impact-result v1**: schema/integrity checked; only its recorded
  assets, edges and paths are shown. Unaffected/disconnected assets and full
  graph coverage cannot be recovered. Reanalysis is disabled without a manifest.
- **governance-ci-review-result v1**: validated by the existing review validator;
  exposes recorded conflict states and assessments. A `not_run` lane is missing
  assessment evidence, not a successful or clear result.
- **governance-plan v1/v2**: inspection only. All plan data, including desired
  objects and evidence, is displayed locally as escaped text.

Imported artifacts remain in browser memory only and are cleared on reload.
The server holds no durable session state. Keep original artifacts and export
reports before closing the session. Synthetic labels refer to the explicit
built-in demo; provenance and graph completeness are never certified.

## Local API and boundaries

JSON OpenAPI: `GET /api/openapi.json` (no CDN-based interactive docs).
All POST requests require `Content-Type: application/json` and
`X-Workbench-Request: 1`. The UI and API use the same origin; no CORS wildcard
is enabled. Foreign Origin, Host and cross-site browser requests are rejected.

| Method / endpoint | Input | Output / effect |
| --- | --- | --- |
| `GET /api/health` | None | Local status and limits |
| `POST /api/demo` | `{}` | Existing demo graph + real impact result |
| `POST /api/graph` | `{manifest, namespace}` | Validated dbt graph, no analysis yet |
| `POST /api/impact` | `{manifest, namespace, changes}` | Existing engine result; changes use existing v1 schema |
| `POST /api/result` | Impact v1 artifact | Result-only evidence projection |
| `POST /api/report` | Impact v1 artifact | Existing renderer's downloadable HTML |
| `POST /api/review` | CI review v1 artifact | Validated recorded review evidence |
| `POST /api/plan` | Saved plan v1/v2 artifact | Validated plan inspection |

The workbench view DTO is private to this app; it does not replace a public
contract or introduce another analysis engine. Source imports: dbt loader,
`analyze_downstream_impact`, impact contract builder/validator, HTML renderer,
review validator and saved-plan loader. No CLI subprocess, arbitrary command,
provider discovery, automatic credential loading or remote adapter is called.
Fixed server-owned demo paths and temporary files are the only backend file
reads/writes for artifact processing. Browser filenames never become server paths.
The browser explicitly chooses files to send to this local server; do not run
an untrusted app on the same origin or expose the server beyond loopback.

Limits: **2 MiB** per streamed request (also without Content-Length), **40 levels**
of JSON nesting, **50,000** JSON values, **200 nodes**, **1,000 relationships**.
Invalid artifacts leave the current successful workspace intact. Metadata is
React-escaped text, never injected HTML; the HTML export uses the existing
escaped renderer. Errors omit server paths and tracebacks.

## Validation

From the root, with the optional dev and workbench extras installed:

```powershell
.\.venv-workbench\Scripts\python.exe -m pytest -q tests/test_workbench_api.py tests/test_impact_html.py tests/test_report_cli.py tests/test_offline_impact_demo.py tests/test_plans_artifact.py tests/test_github_action_review.py
.\.venv-workbench\Scripts\python.exe -m ruff check src/governance/workbench tests/test_workbench_api.py
.\.venv-workbench\Scripts\python.exe -m ruff format --check src/governance/workbench tests/test_workbench_api.py
npm --prefix workbench run test
npm --prefix workbench run lint
npm --prefix workbench run build
```

Backend tests deny provider discovery, credentials and remote/database adapters,
check canonical demo output, different selected roots, renderer equality, v1/v2
plan validation, review integrity, bad inputs, size/nesting limits and host/origin
boundaries. Frontend tests use the committed canonical fixture to check identity,
path and relationship fidelity. Visual QA should exercise empty/loading/error
states, selection, paths, imports, export and medium-screen layouts in localhost.

Known scope limits: no complete data catalog, discovery, source editing, persisted
sessions, policy evaluation authoring, automatic before/after diff, risk scoring,
commercial tenant validation or remote reconciliation. Missing provenance and
coverage are unknown, not zero-risk or compliance evidence. No publication or
integration into other branches is implied.

### Acceptance refinements

Relationship tables separate impact dependencies from containment/hierarchy. Use
**Focus edge** to highlight the same stored relationship in the graph and table;
use linked asset names or the inspector's upstream/downstream buttons to navigate.
Focusing a relationship whose endpoints are hidden reveals all node kinds.
The inspector shows the recorded route to the selected asset and expandable
relationship provenance. Initial graph fit follows actual canvas dimensions,
including responsive changes; manual pan/zoom/fit remain available.

Export captures the current analysis artifact at the start of the request. Failed
validation, non-JSON server failures and unavailable-backend errors are reported
without changing that artifact. Tests for these boundaries are in
`src/model.test.ts` and `src/components.test.tsx` under this directory.

### Post-audit acceptance

Asset type filters keep their explicit selection. Focus graph nodes with Tab and
activate them with Enter or Space; the inspector follows the same selection as mouse
clicks. Review lanes distinguish not assessed, assessed findings, no recorded findings,
missing evidence and unknown states. Unassessed lanes do not display numerical zeros
as completed assessment results. Reload clears all in-memory artifacts; export first.
