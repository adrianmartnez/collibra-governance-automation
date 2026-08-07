"""Live Collibra Core REST API v2 adapter boundary.

Authentication modes:
- Basic username/password
- Bearer token (caller-supplied; may originate from JWT/OAuth elsewhere)

This module does not implement OAuth token acquisition, refresh, or caching.
It is contract-tested with mocked HTTP and is not validated against a commercial
tenant in this repository.

Remote-state discovery is paginated and deterministic after retrieval, but
Collibra REST reads are not treated as a transactional snapshot across
concurrent tenant mutations.

Find contracts (Core REST API v2 OpenAPI):
- GET /attributes uses assetId + typeIds (not typeId)
- GET /relations uses sourceId + relationTypeId (not typeId)
- PATCH asset/attribute bodies include matching id
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from governance.integrations.collibra.adapters import CollibraAdapterError
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    CollibraRemoteAsset,
    CollibraRemoteAttribute,
    CollibraRemoteRelationship,
    CollibraRemoteState,
)

DEFAULT_PAGE_SIZE = 100
API_PREFIX = "/rest/2.0"

QueryParams = Mapping[str, Any] | Sequence[tuple[str, Any]]


class LiveCollibraAdapter:
    """HTTP adapter for Collibra Core REST API v2 (scoped, directed updates)."""

    def __init__(
        self,
        *,
        mapping_config: CollibraMappingConfig,
        base_url: str,
        timeout_seconds: float = 10.0,
        username: str | None = None,
        password: str | None = None,
        bearer_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._config = mapping_config
        self._base_url = _normalize_base_url(base_url)
        self._timeout_seconds = timeout_seconds
        self._page_size = page_size
        self._username = username
        self._password = password
        self._bearer_token = bearer_token
        self._auth_mode = _resolve_auth_mode(username, password, bearer_token)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
            verify=True,
            transport=transport,
            headers=self._auth_headers(),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        mapping_config: CollibraMappingConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> LiveCollibraAdapter:
        return cls(
            mapping_config=mapping_config,
            base_url=settings.collibra_base_url,
            timeout_seconds=settings.collibra_timeout_seconds,
            username=settings.collibra_username or None,
            password=settings.collibra_password or None,
            bearer_token=settings.collibra_bearer_token or None,
            transport=transport,
        )

    @property
    def mode(self) -> Literal["live"]:
        return "live"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LiveCollibraAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"LiveCollibraAdapter(mode='live', base_url={self._base_url!r}, "
            f"auth_mode={self._auth_mode!r}, timeout_seconds={self._timeout_seconds})"
        )

    def read_remote_state(self, desired: CollibraDesiredState) -> CollibraRemoteState:
        del desired
        managed_type_refs = set(self._config.asset_type_refs.values())
        local_id_attr = self._config.attribute_type_refs["local_id"]
        managed_attr_types = set(self._config.attribute_type_refs.values())
        managed_rel_types = set(self._config.relation_type_refs.values())

        raw_assets = self._list_assets_in_scope()
        unmanaged_assets = 0
        managed_assets: list[CollibraRemoteAsset] = []
        remote_to_local: dict[str, str] = {}

        candidate_assets: list[dict[str, Any]] = []
        for item in raw_assets:
            remote_id = str(item.get("id") or "")
            domain_ref = _nested_id(item.get("domain")) or str(item.get("domainId") or "")
            type_ref = _nested_id(item.get("type")) or str(item.get("typeId") or "")
            if not remote_id or domain_ref != self._config.domain_ref:
                unmanaged_assets += 1
                continue
            if type_ref not in managed_type_refs:
                unmanaged_assets += 1
                continue
            candidate_assets.append(item)

        for item in candidate_assets:
            remote_id = str(item["id"])
            type_ref = _nested_id(item.get("type")) or str(item.get("typeId") or "")
            domain_ref = _nested_id(item.get("domain")) or str(item.get("domainId") or "")
            attrs = self._list_managed_attributes_for_asset(remote_id, managed_attr_types)
            local_attr = attrs.get(local_id_attr)
            if local_attr is None or not local_attr.value.strip():
                unmanaged_assets += 1
                continue
            local_id = local_attr.value
            remote_to_local[remote_id] = local_id
            managed_assets.append(
                CollibraRemoteAsset(
                    remote_id=remote_id,
                    local_id=local_id,
                    name=str(item.get("name") or ""),
                    display_name=_optional_str(item.get("displayName")),
                    asset_type_ref=type_ref,
                    domain_ref=domain_ref,
                    managed_attributes=tuple(
                        sorted(attrs.values(), key=lambda attr: attr.attribute_type_ref)
                    ),
                )
            )

        managed_remote_ids = set(remote_to_local)
        raw_relations, unmanaged_relationships = self._list_relations_for_managed_sources(
            managed_remote_ids=managed_remote_ids,
            managed_rel_types=managed_rel_types,
        )
        managed_relationships: list[CollibraRemoteRelationship] = []
        for item in raw_relations:
            remote_id = str(item.get("id") or "")
            type_ref = _nested_id(item.get("type")) or str(item.get("typeId") or "")
            source_remote = _nested_id(item.get("source")) or str(item.get("sourceId") or "")
            target_remote = _nested_id(item.get("target")) or str(item.get("targetId") or "")
            if (
                not remote_id
                or type_ref not in managed_rel_types
                or source_remote not in managed_remote_ids
                or target_remote not in managed_remote_ids
            ):
                unmanaged_relationships += 1
                continue
            managed_relationships.append(
                CollibraRemoteRelationship(
                    remote_id=remote_id,
                    source_remote_id=source_remote,
                    target_remote_id=target_remote,
                    source_local_id=remote_to_local[source_remote],
                    target_local_id=remote_to_local[target_remote],
                    relation_type_ref=type_ref,
                    local_key=None,
                )
            )

        return CollibraRemoteState(
            assets=tuple(managed_assets),
            relationships=tuple(managed_relationships),
            unmanaged_assets_ignored=unmanaged_assets,
            unmanaged_relationships_ignored=unmanaged_relationships,
        )

    def create_asset(self, asset: CollibraAssetSpec) -> str:
        payload: dict[str, Any] = {
            "name": asset.name,
            "domainId": asset.domain_ref,
            "typeId": asset.asset_type_ref,
        }
        if asset.display_name is not None:
            payload["displayName"] = asset.display_name
        response = self._request("POST", f"{API_PREFIX}/assets", json=payload)
        remote_id = str(response.get("id") or "")
        if not remote_id:
            raise CollibraAdapterError(
                "create asset response missing id",
                operation="create_asset",
                endpoint_path=f"{API_PREFIX}/assets",
            )
        for attribute in asset.attributes:
            self._create_attribute(remote_id, attribute.attribute_type_ref, attribute.value)
        return remote_id

    def update_asset(
        self,
        remote_id: str,
        asset: CollibraAssetSpec,
        *,
        patch_name: bool = True,
        patch_display_name: bool = True,
    ) -> None:
        patch_body: dict[str, Any] = {"id": remote_id}
        if patch_name:
            patch_body["name"] = asset.name
        if patch_display_name and asset.display_name is not None:
            patch_body["displayName"] = asset.display_name
        if len(patch_body) > 1:
            self._request("PATCH", f"{API_PREFIX}/assets/{remote_id}", json=patch_body)

        managed_types = set(self._config.attribute_type_refs.values())
        existing = self._list_managed_attributes_for_asset(remote_id, managed_types)
        for attribute in asset.attributes:
            if attribute.attribute_type_ref not in managed_types:
                continue
            current = existing.get(attribute.attribute_type_ref)
            if current is None:
                self._create_attribute(
                    remote_id,
                    attribute.attribute_type_ref,
                    attribute.value,
                )
            elif current.value != attribute.value:
                if not current.remote_attribute_id:
                    raise CollibraAdapterError(
                        "managed attribute missing remote id for patch",
                        operation="update_asset",
                        endpoint_path=f"{API_PREFIX}/attributes",
                    )
                self._patch_attribute(current.remote_attribute_id, attribute.value)

    def create_relationship(
        self,
        relationship: CollibraRelationshipSpec,
        *,
        source_remote_id: str,
        target_remote_id: str,
    ) -> str:
        payload = {
            "sourceId": source_remote_id,
            "targetId": target_remote_id,
            "typeId": relationship.relation_type_ref,
        }
        response = self._request("POST", f"{API_PREFIX}/relations", json=payload)
        remote_id = str(response.get("id") or "")
        if not remote_id:
            raise CollibraAdapterError(
                "create relation response missing id",
                operation="create_relationship",
                endpoint_path=f"{API_PREFIX}/relations",
            )
        return remote_id

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_mode == "bearer":
            assert self._bearer_token is not None
            return {"Authorization": f"Bearer {self._bearer_token}"}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: QueryParams | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        auth = None
        if self._auth_mode == "basic":
            auth = httpx.BasicAuth(self._username or "", self._password or "")
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json,
                auth=auth,
            )
        except httpx.HTTPError:
            raise CollibraAdapterError(
                "HTTP transport error",
                operation=method.lower(),
                endpoint_path=path,
            ) from None
        if response.status_code >= 400:
            raise CollibraAdapterError(
                "Collibra API request failed",
                operation=method.lower(),
                status_code=response.status_code,
                endpoint_path=path,
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise CollibraAdapterError(
                "malformed JSON response",
                operation=method.lower(),
                status_code=response.status_code,
                endpoint_path=path,
            ) from None

    def _paginate(self, path: str, params: QueryParams) -> list[dict[str, Any]]:
        offset = 0
        collected: list[dict[str, Any]] = []
        while True:
            page_params = _with_pagination(params, offset=offset, limit=self._page_size)
            payload = self._request("GET", path, params=page_params)
            results = payload.get("results")
            if results is None:
                raise CollibraAdapterError(
                    "list response missing results",
                    operation="get",
                    endpoint_path=path,
                )
            if not isinstance(results, list):
                raise CollibraAdapterError(
                    "list response results must be a list",
                    operation="get",
                    endpoint_path=path,
                )
            collected.extend(item for item in results if isinstance(item, dict))
            if len(results) < self._page_size:
                break
            offset += self._page_size
        return collected

    def _list_assets_in_scope(self) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for type_ref in sorted(set(self._config.asset_type_refs.values())):
            page = self._paginate(
                f"{API_PREFIX}/assets",
                {
                    "domainId": self._config.domain_ref,
                    "typeId": type_ref,
                },
            )
            collected.extend(page)
        collected.sort(key=lambda item: str(item.get("id") or ""))
        return collected

    def _list_managed_attributes_for_asset(
        self,
        asset_id: str,
        managed_attr_types: set[str],
    ) -> dict[str, CollibraRemoteAttribute]:
        params: list[tuple[str, str]] = [("assetId", asset_id)]
        for type_ref in sorted(managed_attr_types):
            params.append(("typeIds", type_ref))
        results = self._paginate(f"{API_PREFIX}/attributes", params)
        by_type: dict[str, CollibraRemoteAttribute] = {}
        for item in results:
            response_asset = _nested_id(item.get("asset")) or str(item.get("assetId") or "")
            if response_asset != asset_id:
                continue
            response_type = _nested_id(item.get("type")) or str(item.get("typeId") or "")
            if response_type not in managed_attr_types:
                continue
            attr_id = str(item.get("id") or "")
            by_type[response_type] = CollibraRemoteAttribute(
                attribute_type_ref=response_type,
                value=_attribute_value(item.get("value")),
                remote_attribute_id=attr_id or None,
            )
        return by_type

    def _list_relations_for_managed_sources(
        self,
        *,
        managed_remote_ids: set[str],
        managed_rel_types: set[str],
    ) -> tuple[list[dict[str, Any]], int]:
        collected: list[dict[str, Any]] = []
        unmanaged = 0
        seen_remote_ids: set[str] = set()
        for source_id in sorted(managed_remote_ids):
            for relation_type in sorted(managed_rel_types):
                page = self._paginate(
                    f"{API_PREFIX}/relations",
                    {
                        "sourceId": source_id,
                        "relationTypeId": relation_type,
                    },
                )
                for item in page:
                    remote_id = str(item.get("id") or "")
                    response_type = _nested_id(item.get("type")) or str(item.get("typeId") or "")
                    response_source = _nested_id(item.get("source")) or str(
                        item.get("sourceId") or ""
                    )
                    if response_type != relation_type or response_source != source_id:
                        unmanaged += 1
                        continue
                    if remote_id and remote_id in seen_remote_ids:
                        continue
                    if remote_id:
                        seen_remote_ids.add(remote_id)
                    collected.append(item)
        collected.sort(key=lambda item: str(item.get("id") or ""))
        return collected, unmanaged

    def _create_attribute(self, asset_id: str, type_ref: str, value: str) -> None:
        self._request(
            "POST",
            f"{API_PREFIX}/attributes",
            json={"assetId": asset_id, "typeId": type_ref, "value": value},
        )

    def _patch_attribute(self, attribute_id: str, value: str) -> None:
        self._request(
            "PATCH",
            f"{API_PREFIX}/attributes/{attribute_id}",
            json={"id": attribute_id, "value": value},
        )


def _with_pagination(params: QueryParams, *, offset: int, limit: int) -> QueryParams:
    if isinstance(params, Mapping):
        return {**params, "offset": offset, "limit": limit}
    return [*params, ("offset", offset), ("limit", limit)]


def _normalize_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("collibra_base_url is required for live mode")
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("collibra_base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("collibra_base_url must not embed credentials")
    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_auth_mode(
    username: str | None,
    password: str | None,
    bearer_token: str | None,
) -> Literal["basic", "bearer"]:
    has_basic = bool(username) or bool(password)
    has_bearer = bool(bearer_token)
    if has_basic and has_bearer:
        raise ValueError("live mode accepts exactly one auth method: basic or bearer")
    if has_bearer:
        if not bearer_token or not bearer_token.strip():
            raise ValueError("collibra_bearer_token is required for bearer auth")
        return "bearer"
    if username and password:
        return "basic"
    raise ValueError(
        "live mode requires exactly one auth method: "
        "COLLIBRA_USERNAME/COLLIBRA_PASSWORD or COLLIBRA_BEARER_TOKEN"
    )


def _nested_id(value: Any) -> str | None:
    if isinstance(value, dict):
        nested = value.get("id")
        return str(nested) if nested else None
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _attribute_value(value: Any) -> str:
    if isinstance(value, dict):
        if "value" in value:
            return str(value["value"])
        return str(value)
    if value is None:
        return ""
    return str(value)
