"""Localhost Collibra contract HTTP server (stdlib, loopback only).

Not a general Collibra emulator and not a commercial-tenant stand-in.
Request history is sanitized and must never include secrets.
"""

from __future__ import annotations

import json
import re
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

CONTRACT_TOKEN = "contract-access-token"
CONTRACT_CLIENT_SECRET = "contract-client-secret"
CONTRACT_PASSWORD = "contract-basic-password"


def _sanitize_text(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    redacted = re.sub(r"(?i)(authorization:\s*)\S+", r"\1***", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", redacted)
    return redacted


class CollibraContractServer:
    """Threading HTTP server bound to 127.0.0.1."""

    def __init__(self, *, scenario: str = "success") -> None:
        self.scenario = scenario
        self.secrets = (CONTRACT_TOKEN, CONTRACT_CLIENT_SECRET, CONTRACT_PASSWORD)
        self.sanitized_requests: list[dict[str, str]] = []
        self.finalize_posts = 0
        self.token_posts = 0
        self._asset_gets = 0
        self._job_polls: dict[str, int] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.assets: list[dict[str, Any]] = []
        self.job_scripts: dict[str, list[dict[str, Any]]] = {}

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        _host, port = self._httpd.server_address
        return f"http://127.0.0.1:{int(port)}"

    @property
    def token_url(self) -> str:
        return f"{self.base_url}/idp/token"

    def __enter__(self) -> CollibraContractServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PATCH(self) -> None:  # noqa: N802
                owner._handle(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        length = int(handler.headers.get("Content-Length") or "0")
        body = handler.rfile.read(length) if length > 0 else b""
        record = {
            "method": handler.command,
            "path": parsed.path,
            "query": parsed.query,
            "body": _sanitize_text(body.decode("utf-8", errors="replace"), self.secrets),
            "authorization": "***" if handler.headers.get("Authorization") else "",
        }
        self.sanitized_requests.append(record)
        try:
            status, payload, extra_headers = self._dispatch(
                handler.command, parsed.path, parsed.query, body, handler.headers
            )
        except _ResetConnection:
            handler.close_connection = True
            try:
                handler.connection.close()
            except OSError:
                return
            return
        if self.scenario == "malformed" and parsed.path.endswith("/application/info"):
            raw = b"{not-json"
            status = 200
            extra_headers = {"Content-Type": "application/json"}
        else:
            raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", extra_headers.get("Content-Type", "application/json"))
        handler.send_header("Content-Length", str(len(raw)))
        for key, value in extra_headers.items():
            if key.lower() != "content-type":
                handler.send_header(key, value)
        handler.end_headers()
        if raw:
            handler.wfile.write(raw)

    def _dispatch(
        self,
        method: str,
        path: str,
        query: str,
        body: bytes,
        headers: Any,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        if self.scenario == "conn_reset" and method == "GET" and path.endswith("/assets"):
            raise _ResetConnection
        if self.scenario == "malformed" and method == "GET" and path.endswith("/application/info"):
            return 200, {}, {}
        if path.endswith("/oauth/v2/token") or path.endswith("/idp/token"):
            return self._token(method, body, headers)
        if method == "GET" and path.endswith("/application/info"):
            return 200, {"version": "contract-test"}, {}
        if method == "GET" and path.endswith("/assets"):
            return self._assets(query)
        if method == "GET" and path.endswith("/attributes"):
            return 200, {"results": []}, {}
        if method == "GET" and path.endswith("/relations"):
            return 200, {"results": []}, {}
        if method == "POST" and path.endswith("/import/json-job"):
            return self._import_job(body, combined=False)
        if "/import/synchronize/" in path and path.endswith("/batch/json-job"):
            return self._import_job(body, combined=False, job_id="job-batch")
        if "/import/synchronize/" in path and path.endswith("/json-job") and "/batch/" not in path:
            return 400, {"error": "combined-endpoint-forbidden"}, {}
        if path.endswith("/finalize/job"):
            return self._finalize(body)
        if method == "GET" and "/jobs/" in path:
            return self._job(path.rsplit("/", 1)[-1])
        if method == "GET" and path.endswith("/errors"):
            return 200, {"results": []}, {}
        if method == "POST" and path.endswith("/assets"):
            return 201, {"id": "asset-contract"}, {}
        if method == "POST" and path.endswith("/attributes"):
            return 201, {"id": "attr-contract"}, {}
        if method == "POST" and path.endswith("/relations"):
            return 201, {"id": "rel-contract"}, {}
        if method == "PATCH":
            return 200, {"id": path.rsplit("/", 1)[-1]}, {}
        return 404, {"error": "not-found"}, {}

    def _token(
        self, method: str, body: bytes, headers: Any
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        if method != "POST":
            return 405, {"error": "method"}, {}
        if self.scenario == "auth_failure":
            return 401, {"error": "unauthorized"}, {}
        self.token_posts += 1
        text = body.decode("utf-8", errors="replace")
        form = parse_qs(text)
        if "client_secret" in text and CONTRACT_CLIENT_SECRET not in text:
            return 401, {"error": "bad-secret"}, {}
        authorization = headers.get("Authorization") or ""
        if (
            "client_secret_basic" in self.scenario
            and not authorization.lower().startswith("basic ")
            and "client_secret" not in form
        ):
            return 401, {"error": "expected-basic"}, {}
        return (
            200,
            {
                "access_token": CONTRACT_TOKEN,
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            {},
        )

    def _assets(self, query: str) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        self._asset_gets += 1
        if self.scenario == "retry_after_delta" and self._asset_gets == 1:
            return 429, {"error": "rate"}, {"Retry-After": "0"}
        if self.scenario == "retry_after_http_date" and self._asset_gets == 1:
            return (
                429,
                {"error": "rate"},
                {"Retry-After": formatdate(timeval=time.time() - 30, usegmt=True)},
            )
        if self.scenario == "status_5xx" and self._asset_gets == 1:
            return 503, {"error": "unavailable"}, {}
        if self.scenario == "expired_token" and self._asset_gets == 1:
            return 401, {"error": "expired"}, {}
        params = parse_qs(query)
        offset = int((params.get("offset") or ["0"])[0])
        limit = int((params.get("limit") or ["100"])[0])
        type_id = (params.get("typeId") or [""])[0]
        domain_id = (params.get("domainId") or [""])[0]
        scoped = self.assets
        if type_id:
            scoped = [
                item
                for item in scoped
                if str((item.get("type") or {}).get("id") or item.get("typeId") or "") == type_id
            ]
        if domain_id:
            scoped = [
                item
                for item in scoped
                if str((item.get("domain") or {}).get("id") or item.get("domainId") or "")
                == domain_id
            ]
        results = scoped[offset : offset + limit]
        return 200, {"results": results}, {}

    def _import_job(
        self, body: bytes, *, combined: bool, job_id: str = "job-import"
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        del combined
        text = body.decode("utf-8", errors="replace")
        required = (
            "continueOnError",
            "false",
            "relationsAction",
            "ADD_OR_IGNORE",
            "attributesAction",
            "REPLACE",
            "simulation",
            "sendNotification",
            "deleteFile",
        )
        if any(item not in text for item in required):
            return 400, {"error": "unsafe-multipart"}, {}
        return 200, {"id": job_id}, {}

    def _finalize(self, body: bytes) -> tuple[int, dict[str, Any], dict[str, str]]:
        self.finalize_posts += 1
        text = body.decode("utf-8", errors="replace")
        if "IGNORE" not in text or "REMOVE_RESOURCES" in text or "CHANGE_STATUS" in text:
            return 400, {"error": "finalizationStrategy-must-be-IGNORE"}, {}
        if self.scenario == "finalize_malformed":
            return 200, {"name": "missing-id"}, {}
        return 200, {"id": "job-finalize"}, {}

    def _job(self, job_id: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        count = self._job_polls.get(job_id, 0) + 1
        self._job_polls[job_id] = count
        script = self.job_scripts.get(job_id)
        if script:
            index = min(count - 1, len(script) - 1)
            payload = dict(script[index])
            payload.setdefault("id", job_id)
            return 200, payload, {}
        if self.scenario == "job_delay" and count < 3:
            return 200, {"id": job_id, "state": "RUNNING"}, {}
        if self.scenario == "job_error":
            return 200, {"id": job_id, "state": "ERROR"}, {}
        if self.scenario == "job_completed_with_error":
            return 200, {"id": job_id, "state": "COMPLETED", "result": "COMPLETED_WITH_ERROR"}, {}
        if self.scenario == "job_canceling":
            if count == 1:
                return 200, {"id": job_id, "state": "CANCELING"}, {}
            return 200, {"id": job_id, "state": "CANCELED"}, {}
        if self.scenario == "job_aborted":
            return 200, {"id": job_id, "state": "COMPLETED", "result": "ABORTED"}, {}
        if self.scenario == "job_unknown":
            return 200, {"id": job_id, "state": "NEW_VENDOR_STATE"}, {}
        if self.scenario == "finalize_failure" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "COMPLETED", "result": "FAILURE"}, {}
        if self.scenario == "finalize_unknown" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "NEW_STATE"}, {}
        if self.scenario == "finalize_timeout" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "RUNNING"}, {}
        return 200, {"id": job_id, "state": "COMPLETED", "result": "SUCCESS"}, {}


class _ResetConnection(Exception):
    """Signal the HTTP handler to drop the TCP connection."""
