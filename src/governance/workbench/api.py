"""Loopback-only workbench API. No provider discovery, credentials or remote I/O."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from governance.domain import analyze_downstream_impact
from governance.github_ci.review import validate_review_result
from governance.impact import (
    build_impact_result,
    identity_from_dict,
    verify_impact_result_integrity,
)
from governance.impact.contracts import validate_changes_document
from governance.impact.html import render_impact_report
from governance.integrations.dbt import load_dbt_graph
from governance.plans import load_saved_plan
from governance.workbench.projection import project_workspace

RESOURCES = files("governance.workbench").joinpath("resources")
MAX_BYTES = 2 * 1024 * 1024
ORIGINS = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (5173, 8765)}
HOSTS = {origin.removeprefix("http://") for origin in ORIGINS}


def _budget(value: Any, depth: int = 0) -> int:
    if depth > 40:
        raise HTTPException(413, "Input nesting exceeds 40 levels.")
    values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    count = 1 + sum(_budget(child, depth + 1) for child in values)
    if count > 50000:
        raise HTTPException(413, "Input exceeds the local workbench object limit.")
    return count


async def document(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Send a JSON artifact, not a filename or form.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise HTTPException(413, "Maximum input size is 2 MiB.")
    try:
        value = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        _budget(value)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise HTTPException(422, "Invalid JSON or excessive nesting.") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "A JSON object is required.")
    return value


def graph_from_manifest(manifest: dict[str, Any], namespace: str):
    if not isinstance(manifest, dict) or not isinstance(namespace, str) or not namespace.strip():
        raise HTTPException(422, "A dbt manifest object and non-empty namespace are required.")
    if len(namespace) > 200:
        raise HTTPException(422, "Namespace is too long.")
    with TemporaryDirectory(prefix="governance-workbench-") as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        graph = load_dbt_graph(path, namespace=namespace)
    if len(graph.nodes) > 200 or len(graph.edges) > 1000:
        raise HTTPException(413, "Workbench supports up to 200 nodes and 1,000 relationships.")
    return graph


def workspace(graph: Any, result: dict[str, Any] | None, *, synthetic: bool = False):
    projected = project_workspace(graph, result, synthetic=synthetic)
    if len(projected["nodes"]) > 200 or len(projected["edges"]) > 1000:
        raise HTTPException(413, "Result evidence exceeds 200 nodes or 1,000 relationships.")
    return projected


def create_app(demo_root: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Keep extracted package resources alive for the entire server lifetime.
        with as_file(RESOURCES.joinpath("frontend")) as frontend:
            app.mount("/", StaticFiles(directory=frontend, html=True), name="workbench")
            yield

    app = FastAPI(
        lifespan=lifespan,
        title="Governance Control Center • local read-only API",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    demo_root = demo_root or RESOURCES.joinpath("demo")

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("host") not in HOSTS:
            return JSONResponse({"detail": "Only the documented loopback hosts are accepted."}, 400)
        origin = request.headers.get("origin")
        if (origin and origin not in ORIGINS) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            return JSONResponse({"detail": "Cross-origin access is forbidden."}, 403)
        if request.method not in {"GET", "HEAD", "POST"}:
            return JSONResponse({"detail": "Operation not supported."}, 405)
        if request.method == "POST" and request.headers.get("x-workbench-request") != "1":
            return JSONResponse({"detail": "Workbench request header required."}, 403)
        try:
            if int(request.headers.get("content-length", "0")) > MAX_BYTES:
                return JSONResponse({"detail": "Maximum input size is 2 MiB."}, 413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, 400)
        try:
            response = await call_next(request)
        except HTTPException:
            raise
        except Exception as exc:
            errors = getattr(exc, "errors", None)
            if errors and isinstance(errors, (list, tuple)):
                message = "; ".join(f"{e.path or '/'}: {e.message}" for e in errors[:5])
                return JSONResponse({"detail": message[:1200]}, 422)
            if isinstance(exc, (ValueError, TypeError, KeyError, RecursionError)):
                return JSONResponse(
                    {"detail": "Invalid artifact; check its schema and required fields."}, 422
                )
            # Never disclose server paths, tracebacks or credentials.
            return JSONResponse(
                {"detail": "Local operation failed. Check installation and input limits."}, 500
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/api/health")
    def health():
        return {"status": "local", "writes_performed": 0, "max_bytes": MAX_BYTES}

    @app.post("/api/demo")
    async def demo(request: Request):
        await document(request)
        if not (demo_root / "manifest.json").is_file():
            raise HTTPException(
                503, "Packaged offline demo resources are missing. Reinstall the distribution."
            )
        manifest = json.loads((demo_root / "manifest.json").read_text(encoding="utf-8"))
        changes = json.loads((demo_root / "changes.json").read_text(encoding="utf-8"))
        graph = graph_from_manifest(manifest, "offline-demo")
        roots = tuple(identity_from_dict(n) for n in changes["changed_nodes"])
        result = build_impact_result(graph=graph, impact=analyze_downstream_impact(graph, roots))
        return {
            **workspace(graph, result, synthetic=True),
            "manifest": manifest,
            "namespace": "offline-demo",
        }

    @app.post("/api/graph")
    async def graph_upload(request: Request):
        value = await document(request)
        graph = graph_from_manifest(value.get("manifest"), value.get("namespace"))
        return {
            **workspace(graph, None),
            "manifest": value["manifest"],
            "namespace": value["namespace"],
        }

    @app.post("/api/impact")
    async def impact(request: Request):
        value = await document(request)
        changes = value.get("changes")
        validate_changes_document(changes)
        namespace = value.get("namespace")
        graph = graph_from_manifest(value.get("manifest"), namespace)
        roots = tuple(identity_from_dict(n) for n in changes["changed_nodes"])
        if any(n.namespace != namespace for root in roots for n in (root, *root.ancestors())):
            raise HTTPException(422, "Changed identities must match the graph namespace.")
        result = build_impact_result(graph=graph, impact=analyze_downstream_impact(graph, roots))
        return workspace(graph, result)

    @app.post("/api/result")
    async def result_upload(request: Request):
        value = await document(request)
        verify_impact_result_integrity(value)
        return workspace(None, value)

    @app.post("/api/report")
    async def report(request: Request):
        value = await document(request)
        return Response(
            render_impact_report(value),
            media_type="text/html",
            headers={"Content-Disposition": 'attachment; filename="governance-impact-report.html"'},
        )

    @app.post("/api/plan")
    async def plan(request: Request):
        value = await document(request)
        with TemporaryDirectory(prefix="governance-plan-inspection-") as directory:
            path = Path(directory) / "artifact.gplan"
            path.write_text(json.dumps(value), encoding="utf-8")
            saved = load_saved_plan(path)
        return saved.to_dict()

    @app.post("/api/review")
    async def review(request: Request):
        return validate_review_result(await document(request))

    @app.api_route("/api/{unknown:path}", methods=["GET", "POST"])
    def unknown_api(unknown: str):
        raise HTTPException(404, "No such workbench operation.")

    return app


app = create_app()
