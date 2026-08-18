"""Localhost Collibra contract HTTP server (stdlib, loopback only).

Not a general Collibra emulator and not a commercial-tenant stand-in.
Request history is sanitized and must never include secrets.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from email import message_from_bytes
from email.policy import default as email_default_policy
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

CONTRACT_TOKEN = "contract-access-token"
CONTRACT_CLIENT_ID = "contract-client"
CONTRACT_CLIENT_SECRET = "contract-client-secret"
CONTRACT_PASSWORD = "contract-basic-password"
CONTRACT_SCOPE = "contract-scope"


def _sanitize_text(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    redacted = re.sub(r"(?i)(authorization:\s*)\S+", r"\1***", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", redacted)
    redacted = re.sub(r"(?i)(client_secret=)[^&\s]+", r"\1***", redacted)
    redacted = re.sub(r"(?i)(password=)[^&\s]+", r"\1***", redacted)
    return redacted


def _json_keys(payload: dict[str, Any]) -> list[str]:
    return sorted(str(key) for key in payload)


class CollibraContractServer:
    """Threading HTTP server bound to 127.0.0.1."""

    def __init__(self, *, scenario: str = "success") -> None:
        self.scenario = scenario
        self.secrets = (CONTRACT_TOKEN, CONTRACT_CLIENT_SECRET, CONTRACT_PASSWORD)
        self.sanitized_requests: list[dict[str, Any]] = []
        self.finalize_posts = 0
        self.token_posts = 0
        self._asset_gets = 0
        self._job_polls: dict[str, int] = {}
        self._import_job_counter = 0
        self._sync_batch_counter = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.assets: list[dict[str, Any]] = []
        self.attributes: list[dict[str, Any]] = []
        self.job_scripts: dict[str, list[dict[str, Any]]] = {}
        self.collision_lookups: list[dict[str, str]] = []
        self.core_rest_writes: list[dict[str, Any]] = []
        self.token_auth_modes: list[str] = []

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
        authorization = handler.headers.get("Authorization") or ""
        content_type = handler.headers.get("Content-Type") or ""
        record: dict[str, Any] = {
            "method": handler.command,
            "path": parsed.path,
            "query": parsed.query,
            "body": _sanitize_text(body.decode("utf-8", errors="replace"), self.secrets),
            "authorization": "***" if authorization else "",
            "has_authorization": bool(authorization.strip()),
            "authorization_scheme": (
                "Basic"
                if authorization.lower().startswith("basic ")
                else ("Bearer" if authorization.lower().startswith("bearer ") else "")
            ),
            "content_type": content_type,
        }
        self.sanitized_requests.append(record)
        try:
            status, payload, extra_headers, record_updates = self._dispatch(
                handler.command,
                parsed.path,
                parsed.query,
                body,
                handler.headers,
            )
        except _ResetConnection:
            handler.close_connection = True
            try:
                handler.connection.close()
            except OSError:
                return
            return
        if record_updates:
            record.update(record_updates)
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
    ) -> tuple[int, dict[str, Any] | None, dict[str, str], dict[str, Any]]:
        if self.scenario == "conn_reset" and method == "GET" and path.endswith("/assets"):
            raise _ResetConnection
        if self.scenario == "malformed" and method == "GET" and path.endswith("/application/info"):
            return 200, {}, {}, {}
        if path.endswith("/oauth/v2/token") or path.endswith("/idp/token"):
            return self._token(method, path, body, headers)
        if method == "GET" and path.endswith("/application/info"):
            return 200, {"version": "contract-test"}, {}, {}
        if method == "GET" and path.endswith("/assets"):
            return self._assets(query)
        if method == "GET" and path.endswith("/attributes"):
            return self._attributes(query)
        if method == "GET" and path.endswith("/relations"):
            return self._relations(query)
        if method == "POST" and path.endswith("/import/json-job"):
            return self._import_job(body, headers.get("Content-Type", ""), sync_batch=False)
        if "/import/synchronize/" in path and path.endswith("/batch/json-job"):
            return self._import_job(body, headers.get("Content-Type", ""), sync_batch=True)
        if "/import/synchronize/" in path and path.endswith("/json-job") and "/batch/" not in path:
            return 400, {"error": "combined-endpoint-forbidden"}, {}, {}
        if path.endswith("/finalize/job"):
            return self._finalize(body)
        if method == "GET" and "/jobs/" in path:
            return self._job(path.rsplit("/", 1)[-1])
        if method == "GET" and path.endswith("/errors"):
            return self._import_errors(path)
        if method == "POST" and path.endswith("/assets"):
            return self._post_asset(body)
        if method == "POST" and path.endswith("/attributes"):
            return self._post_attribute(body)
        if method == "POST" and path.endswith("/relations"):
            return self._post_relation(body)
        if method == "PATCH" and "/assets/" in path:
            return self._patch_asset(path, body)
        if method == "PATCH" and "/attributes/" in path:
            return self._patch_attribute(path, body)
        return 404, {"error": "not-found"}, {}, {}

    def _token(
        self, method: str, path: str, body: bytes, headers: Any
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        if method != "POST":
            return 405, {"error": "method"}, {}, {}
        if self.scenario == "auth_failure":
            return 401, {"error": "unauthorized"}, {}, {}
        content_type = (headers.get("Content-Type") or "").lower()
        if "application/x-www-form-urlencoded" not in content_type:
            return 415, {"error": "content-type"}, {}, {}
        self.token_posts += 1
        text = body.decode("utf-8", errors="replace")
        form = parse_qs(text, keep_blank_values=True)
        grant_type = (form.get("grant_type") or [""])[0]
        if grant_type != "client_credentials":
            return 400, {"error": "grant_type"}, {}, {}
        authorization = headers.get("Authorization") or ""
        is_basic = authorization.lower().startswith("basic ")
        is_native = path.endswith("/oauth/v2/token")
        scope = (form.get("scope") or [""])[0]
        if scope and scope != CONTRACT_SCOPE:
            return 400, {"error": "scope"}, {}, {}
        if is_native:
            if is_basic:
                return 401, {"error": "native-oauth-rejects-basic"}, {}, {}
            client_id = (form.get("client_id") or [""])[0]
            client_secret = (form.get("client_secret") or [""])[0]
            if client_id != CONTRACT_CLIENT_ID or client_secret != CONTRACT_CLIENT_SECRET:
                return 401, {"error": "bad-credentials"}, {}, {}
            self.token_auth_modes.append("native")
            return (
                200,
                {
                    "access_token": CONTRACT_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
                {},
                {"token_auth_mode": "native"},
            )
        if is_basic:
            if "client_id" in form or "client_secret" in form:
                return 400, {"error": "basic-auth-forbids-body-credentials"}, {}, {}
            encoded = authorization.split(" ", 1)[1].strip()
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return 401, {"error": "bad-basic"}, {}, {}
            if ":" not in decoded:
                return 401, {"error": "bad-basic"}, {}, {}
            client_id, client_secret = decoded.split(":", 1)
            if client_id != CONTRACT_CLIENT_ID or client_secret != CONTRACT_CLIENT_SECRET:
                return 401, {"error": "bad-credentials"}, {}, {}
            self.token_auth_modes.append("client_secret_basic")
            return (
                200,
                {
                    "access_token": CONTRACT_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
                {},
                {
                    "token_auth_mode": "client_secret_basic",
                    "authorization_scheme": "Basic",
                    "has_authorization": True,
                },
            )
        if authorization.strip():
            return 401, {"error": "post-auth-forbids-authorization-header"}, {}, {}
        client_id = (form.get("client_id") or [""])[0]
        client_secret = (form.get("client_secret") or [""])[0]
        if client_id != CONTRACT_CLIENT_ID or client_secret != CONTRACT_CLIENT_SECRET:
            return 401, {"error": "bad-credentials"}, {}, {}
        self.token_auth_modes.append("client_secret_post")
        return (
            200,
            {
                "access_token": CONTRACT_TOKEN,
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            {},
            {"token_auth_mode": "client_secret_post", "has_authorization": False},
        )

    def _assets(
        self, query: str
    ) -> tuple[int, dict[str, Any] | None, dict[str, str], dict[str, Any]]:
        self._asset_gets += 1
        if self.scenario == "retry_after_delta" and self._asset_gets == 1:
            return 429, {"error": "rate"}, {"Retry-After": "0"}, {}
        if self.scenario == "retry_after_http_date" and self._asset_gets == 1:
            return (
                429,
                {"error": "rate"},
                {"Retry-After": formatdate(timeval=time.time() - 30, usegmt=True)},
                {},
            )
        if self.scenario == "status_5xx" and self._asset_gets == 1:
            return 503, {"error": "unavailable"}, {}, {}
        if self.scenario == "expired_token" and self._asset_gets == 1:
            return 401, {"error": "expired"}, {}, {}
        params = parse_qs(query, keep_blank_values=True)
        if "name" in params:
            reject = self._validate_collision_query(params)
            if reject is not None:
                return reject[0], reject[1], reject[2], {}
            name = params["name"][0]
            domain_id = (params.get("domainId") or [""])[0]
            self.collision_lookups.append({"name": name, "domainId": domain_id})
            return 200, {"results": [], "total": 0}, {}, {"collision_name": name}
        if "typeId" in params and "name" not in params:
            offset = int((params.get("offset") or ["0"])[0])
            limit = int((params.get("limit") or ["100"])[0])
            type_id = params["typeId"][0]
            domain_id = (params.get("domainId") or [""])[0]
            scoped = self.assets
            if type_id:
                scoped = [
                    item
                    for item in scoped
                    if str((item.get("type") or {}).get("id") or item.get("typeId") or "")
                    == type_id
                ]
            if domain_id:
                scoped = [
                    item
                    for item in scoped
                    if str((item.get("domain") or {}).get("id") or item.get("domainId") or "")
                    == domain_id
                ]
            results = scoped[offset : offset + limit]
            return 200, {"results": results}, {}, {}
        return 400, {"error": "unsupported-assets-query"}, {}, {}

    def _validate_collision_query(
        self, params: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        if "typeId" in params:
            return 400, {"error": "collision-forbids-typeId"}, {}
        name_match = (params.get("nameMatchMode") or [""])[0]
        if name_match != "EXACT":
            return 400, {"error": "collision-requires-EXACT-nameMatchMode"}, {}
        domain_id = (params.get("domainId") or [""])[0]
        if not domain_id:
            return 400, {"error": "collision-requires-domainId"}, {}
        exclude_meta = (params.get("excludeMeta") or [""])[0]
        if exclude_meta != "false":
            return 400, {"error": "collision-requires-excludeMeta-false"}, {}
        return None

    def _attributes(self, query: str) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        params = parse_qs(query, keep_blank_values=True)
        if "typeId" in params and "typeIds" not in params:
            return 400, {"error": "attributes-require-typeIds-not-typeId"}, {}, {}
        if "assetId" not in params:
            return 400, {"error": "attributes-require-assetId"}, {}, {}
        offset = int((params.get("offset") or ["0"])[0])
        limit = int((params.get("limit") or ["100"])[0])
        asset_id = params["assetId"][0]
        allowed_type_ids = set(params.get("typeIds") or [])
        scoped = [
            item
            for item in self.attributes
            if str(item.get("assetId") or (item.get("asset") or {}).get("id") or "") == asset_id
            and (
                not allowed_type_ids
                or str(item.get("typeId") or (item.get("type") or {}).get("id") or "")
                in allowed_type_ids
            )
        ]
        results = scoped[offset : offset + limit]
        return 200, {"results": results, "offset": offset, "limit": limit}, {}, {}

    def _relations(self, query: str) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        params = parse_qs(query, keep_blank_values=True)
        if "typeId" in params:
            return 400, {"error": "relations-forbid-typeId"}, {}, {}
        if "sourceId" not in params or "relationTypeId" not in params:
            return 400, {"error": "relations-require-sourceId-relationTypeId"}, {}, {}
        offset = int((params.get("offset") or ["0"])[0])
        limit = int((params.get("limit") or ["100"])[0])
        return 200, {"results": [], "offset": offset, "limit": limit}, {}, {}

    def _parse_json_body(self, body: bytes) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid-json"
        if not isinstance(payload, dict):
            return None, "json-object-required"
        return payload, None

    def _post_asset(
        self, body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        payload, error = self._parse_json_body(body)
        if error or payload is None:
            return 400, {"error": error or "invalid"}, {}, {}
        required = {"name", "domainId", "typeId"}
        if not required.issubset(payload.keys()):
            return 400, {"error": "asset-shape"}, {}, {}
        for key in ("name", "domainId", "typeId"):
            if not isinstance(payload[key], str):
                return 400, {"error": "asset-string-fields"}, {}, {}
        if "displayName" in payload and not isinstance(payload["displayName"], str):
            return 400, {"error": "asset-displayName"}, {}, {}
        safe = {
            "name": payload["name"],
            "domainId": payload["domainId"],
            "typeId": payload["typeId"],
            "json_keys": _json_keys(payload),
        }
        self.core_rest_writes.append({"kind": "asset_create", **safe})
        return 201, {"id": "asset-contract"}, {}, {"json_keys": safe["json_keys"]}

    def _post_attribute(
        self, body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        payload, error = self._parse_json_body(body)
        if error or payload is None:
            return 400, {"error": error or "invalid"}, {}, {}
        required = {"assetId", "typeId", "value"}
        if set(payload.keys()) != required:
            return 400, {"error": "attribute-shape"}, {}, {}
        for key in required:
            if not isinstance(payload[key], str):
                return 400, {"error": "attribute-string-fields"}, {}, {}
        safe = {
            "assetId": payload["assetId"],
            "typeId": payload["typeId"],
            "json_keys": _json_keys(payload),
        }
        self.core_rest_writes.append({"kind": "attribute_create", **safe})
        return 201, {"id": "attr-contract"}, {}, {"json_keys": safe["json_keys"]}

    def _post_relation(
        self, body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        payload, error = self._parse_json_body(body)
        if error or payload is None:
            return 400, {"error": error or "invalid"}, {}, {}
        required = {"sourceId", "targetId", "typeId"}
        if set(payload.keys()) != required:
            return 400, {"error": "relation-shape"}, {}, {}
        for key in required:
            if not isinstance(payload[key], str):
                return 400, {"error": "relation-string-fields"}, {}, {}
        safe = {
            "sourceId": payload["sourceId"],
            "targetId": payload["targetId"],
            "typeId": payload["typeId"],
            "json_keys": _json_keys(payload),
        }
        self.core_rest_writes.append({"kind": "relation_create", **safe})
        return 201, {"id": "rel-contract"}, {}, {"json_keys": safe["json_keys"]}

    def _patch_asset(
        self, path: str, body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        remote_id = path.rsplit("/", 1)[-1]
        payload, error = self._parse_json_body(body)
        if error or payload is None:
            return 400, {"error": error or "invalid"}, {}, {}
        if payload.get("id") != remote_id:
            return 400, {"error": "asset-patch-id-mismatch"}, {}, {}
        allowed = {"id", "name", "displayName"}
        if not set(payload.keys()).issubset(allowed):
            return 400, {"error": "asset-patch-fields"}, {}, {}
        self.core_rest_writes.append(
            {"kind": "asset_patch", "id": remote_id, "json_keys": _json_keys(payload)}
        )
        return 200, {"id": remote_id}, {}, {"json_keys": _json_keys(payload)}

    def _patch_attribute(
        self, path: str, body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        remote_id = path.rsplit("/", 1)[-1]
        payload, error = self._parse_json_body(body)
        if error or payload is None:
            return 400, {"error": error or "invalid"}, {}, {}
        if payload.get("id") != remote_id:
            return 400, {"error": "attribute-patch-id-mismatch"}, {}, {}
        if set(payload.keys()) != {"id", "value"} or not isinstance(payload["value"], str):
            return 400, {"error": "attribute-patch-shape"}, {}, {}
        self.core_rest_writes.append({"kind": "attribute_patch", "id": remote_id})
        return 200, {"id": remote_id}, {}, {"json_keys": _json_keys(payload)}

    def _parse_multipart_import(
        self, body: bytes, content_type: str
    ) -> tuple[list[Any] | None, str | None]:
        if "multipart/form-data" not in content_type.lower():
            return None, "multipart-required"
        msg = message_from_bytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body,
            policy=email_default_policy,
        )
        if not msg.is_multipart():
            return None, "multipart-required"
        fields: dict[str, str] = {}
        commands: list[Any] | None = None
        for part in msg.iter_parts():
            disposition = part.get("Content-Disposition", "")
            if "name=" not in disposition:
                continue
            name_match = re.search(r'name="([^"]+)"', disposition)
            if not name_match:
                continue
            name = name_match.group(1)
            payload = part.get_payload(decode=True) or b""
            if name == "file":
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None, "import-json-invalid"
                if not isinstance(parsed, list) or not parsed:
                    return None, "import-json-empty"
                commands = parsed
            else:
                fields[name] = payload.decode("utf-8", errors="replace")
        required = {
            "continueOnError": "false",
            "relationsAction": "ADD_OR_IGNORE",
            "attributesAction": "REPLACE",
            "simulation": "false",
            "sendNotification": "false",
            "deleteFile": "false",
        }
        for key, value in required.items():
            if fields.get(key) != value:
                return None, "unsafe-multipart"
        return commands, None

    def _import_job(
        self, body: bytes, content_type: str, *, sync_batch: bool
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        commands, error = self._parse_multipart_import(body, content_type)
        if error is not None:
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
                return 400, {"error": "unsafe-multipart"}, {}, {}
            return 400, {"error": error}, {}, {}
        if sync_batch:
            job_id = f"job-batch-{self._sync_batch_counter}"
            self._sync_batch_counter += 1
        else:
            job_id = f"job-import-{self._import_job_counter}"
            self._import_job_counter += 1
        return 200, {"id": job_id}, {}, {"import_command_count": str(len(commands or []))}

    def _finalize(self, body: bytes) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        self.finalize_posts += 1
        text = body.decode("utf-8", errors="replace")
        if "IGNORE" not in text or "REMOVE_RESOURCES" in text or "CHANGE_STATUS" in text:
            return 400, {"error": "finalizationStrategy-must-be-IGNORE"}, {}, {}
        if self.scenario == "finalize_malformed":
            return 200, {"name": "missing-id"}, {}, {}
        return 200, {"id": "job-finalize"}, {}, {}

    def _import_errors(
        self, path: str
    ) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        del path
        return (
            200,
            {
                "total": 5,
                "offset": 0,
                "limit": 2,
                "results": [{"code": "E1"}, {"code": "E2"}],
            },
            {},
            {},
        )

    def _job(self, job_id: str) -> tuple[int, dict[str, Any], dict[str, str], dict[str, Any]]:
        count = self._job_polls.get(job_id, 0) + 1
        self._job_polls[job_id] = count
        script = self.job_scripts.get(job_id)
        if script:
            index = min(count - 1, len(script) - 1)
            payload = dict(script[index])
            payload.setdefault("id", job_id)
            return 200, payload, {}, {}
        if self.scenario == "second_batch_failure" and (
            job_id.endswith("-1") or job_id in {"job-import-1", "job-batch-1"}
        ):
            return 200, {"id": job_id, "state": "COMPLETED", "result": "FAILURE"}, {}, {}
        if self.scenario == "job_delay" and count < 3:
            return 200, {"id": job_id, "state": "RUNNING"}, {}, {}
        if self.scenario == "job_error":
            return 200, {"id": job_id, "state": "ERROR"}, {}, {}
        if self.scenario == "job_completed_with_error":
            return (
                200,
                {"id": job_id, "state": "COMPLETED", "result": "COMPLETED_WITH_ERROR"},
                {},
                {},
            )
        if self.scenario == "job_canceling":
            if count == 1:
                return 200, {"id": job_id, "state": "CANCELING"}, {}, {}
            return 200, {"id": job_id, "state": "CANCELED"}, {}, {}
        if self.scenario == "job_aborted":
            return 200, {"id": job_id, "state": "COMPLETED", "result": "ABORTED"}, {}, {}
        if self.scenario == "job_unknown":
            return 200, {"id": job_id, "state": "NEW_VENDOR_STATE"}, {}, {}
        if self.scenario == "finalize_failure" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "COMPLETED", "result": "FAILURE"}, {}, {}
        if self.scenario == "finalize_unknown" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "NEW_STATE"}, {}, {}
        if self.scenario == "finalize_timeout" and job_id == "job-finalize":
            return 200, {"id": job_id, "state": "RUNNING"}, {}, {}
        return 200, {"id": job_id, "state": "COMPLETED", "result": "SUCCESS"}, {}, {}


class _ResetConnection(Exception):
    """Signal the HTTP handler to drop the TCP connection."""
