"""Live Collibra adapter contract tests using strict httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from governance.config import Settings, load_settings
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    LiveCollibraAdapter,
    build_collibra_adapter,
    mock_mapping_config,
)

SECRET_TOKEN = "super-secret-bearer-token-do-not-leak"
SECRET_PASSWORD = "super-secret-password-do-not-leak"


def _settings(**overrides: Any) -> Settings:
    base = {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "governance_demo",
        "postgres_user": "postgres",
        "postgres_password": "postgres",
        "postgres_source_name": "governance-demo",
        "inventory_output_path": "artifacts/metadata-inventory.json",
        "collibra_mode": "live",
        "collibra_base_url": "https://collibra.example.invalid",
        "collibra_timeout_seconds": 10.0,
    }
    base.update(overrides)
    return Settings(**base)


class _FakeCollibra:
    """Strict Core REST v2 stand-in: rejects obsolete/wrong find/patch keys."""

    def __init__(self, *, page_size: int = 2) -> None:
        self.page_size = page_size
        self.assets: list[dict[str, Any]] = []
        self.attributes: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self._asset_seq = 0
        self._attr_seq = 0
        self._rel_seq = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = urlparse(str(request.url)).path
        method = request.method.upper()
        query = parse_qs(urlparse(str(request.url)).query)

        if method == "GET" and path.endswith("/assets"):
            return self._list_assets(query)
        if method == "GET" and path.endswith("/attributes"):
            return self._list_attributes(query)
        if method == "GET" and path.endswith("/relations"):
            return self._list_relations(query)
        if method == "POST" and path.endswith("/assets"):
            body = json.loads(request.content.decode())
            self._asset_seq += 1
            asset_id = f"asset-{self._asset_seq}"
            self.assets.append(
                {
                    "id": asset_id,
                    "name": body["name"],
                    "displayName": body.get("displayName"),
                    "domain": {"id": body["domainId"]},
                    "type": {"id": body["typeId"]},
                }
            )
            return httpx.Response(201, json={"id": asset_id})
        if method == "POST" and path.endswith("/attributes"):
            body = json.loads(request.content.decode())
            self._attr_seq += 1
            attr_id = f"attr-{self._attr_seq}"
            self.attributes.append(
                {
                    "id": attr_id,
                    "asset": {"id": body["assetId"]},
                    "type": {"id": body["typeId"]},
                    "value": body["value"],
                }
            )
            return httpx.Response(201, json={"id": attr_id})
        if method == "PATCH" and "/attributes/" in path:
            attr_id = path.rsplit("/", 1)[-1]
            body = json.loads(request.content.decode())
            if body.get("id") != attr_id or "value" not in body:
                return httpx.Response(400, json={"error": "invalid ChangeAttributeRequest"})
            for attribute in self.attributes:
                if attribute["id"] == attr_id:
                    attribute["value"] = body["value"]
                    return httpx.Response(200, json={"id": attr_id})
            return httpx.Response(404, json={"error": "missing"})
        if method == "PATCH" and "/assets/" in path:
            asset_id = path.rsplit("/", 1)[-1]
            body = json.loads(request.content.decode())
            if body.get("id") != asset_id:
                return httpx.Response(400, json={"error": "invalid ChangeAssetRequest"})
            for asset in self.assets:
                if asset["id"] == asset_id:
                    if "name" in body:
                        asset["name"] = body["name"]
                    if "displayName" in body:
                        asset["displayName"] = body["displayName"]
            return httpx.Response(200, json={"id": asset_id})
        if method == "POST" and path.endswith("/relations"):
            body = json.loads(request.content.decode())
            self._rel_seq += 1
            rel_id = f"rel-{self._rel_seq}"
            self.relations.append(
                {
                    "id": rel_id,
                    "source": {"id": body["sourceId"]},
                    "target": {"id": body["targetId"]},
                    "type": {"id": body["typeId"]},
                }
            )
            return httpx.Response(201, json={"id": rel_id})
        if method == "DELETE":
            return httpx.Response(500, json={"error": "delete not allowed in tests"})
        return httpx.Response(404, json={"error": "not found"})

    def _page(self, items: list[dict[str, Any]], query: dict[str, list[str]]) -> httpx.Response:
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", [str(self.page_size)])[0])
        page = items[offset : offset + limit]
        return httpx.Response(200, json={"results": page, "total": len(items)})

    def _list_assets(self, query: dict[str, list[str]]) -> httpx.Response:
        filtered = list(self.assets)
        if "domainId" in query:
            domain_id = query["domainId"][0]
            filtered = [
                item for item in filtered if (item.get("domain") or {}).get("id") == domain_id
            ]
        if "typeId" in query:
            type_id = query["typeId"][0]
            filtered = [item for item in filtered if (item.get("type") or {}).get("id") == type_id]
        return self._page(filtered, query)

    def _list_attributes(self, query: dict[str, list[str]]) -> httpx.Response:
        if "typeId" in query:
            return httpx.Response(400, json={"error": "typeId is not a Find Attributes param"})
        if "assetId" not in query or "typeIds" not in query:
            return httpx.Response(
                400,
                json={"error": "assetId and typeIds are required"},
            )
        asset_id = query["assetId"][0]
        type_ids = set(query["typeIds"])
        filtered = [
            item
            for item in self.attributes
            if (item.get("asset") or {}).get("id") == asset_id
            and (item.get("type") or {}).get("id") in type_ids
        ]
        return self._page(filtered, query)

    def _list_relations(self, query: dict[str, list[str]]) -> httpx.Response:
        if "typeId" in query:
            return httpx.Response(400, json={"error": "typeId is not a Find Relations param"})
        if "sourceId" not in query or "relationTypeId" not in query:
            return httpx.Response(
                400,
                json={"error": "sourceId and relationTypeId are required"},
            )
        source_id = query["sourceId"][0]
        relation_type = query["relationTypeId"][0]
        filtered = [
            item
            for item in self.relations
            if (item.get("source") or {}).get("id") == source_id
            and (item.get("type") or {}).get("id") == relation_type
        ]
        return self._page(filtered, query)


def _adapter(fake: _FakeCollibra, *, bearer: bool = True) -> LiveCollibraAdapter:
    config = mock_mapping_config()
    if bearer:
        settings = _settings(collibra_bearer_token=SECRET_TOKEN)
    else:
        settings = _settings(
            collibra_username="demo-user",
            collibra_password=SECRET_PASSWORD,
        )
    return LiveCollibraAdapter.from_settings(
        settings,
        config,
        transport=httpx.MockTransport(fake.handler),
    )


def _attr_gets(fake: _FakeCollibra) -> list[httpx.Request]:
    return [
        request
        for request in fake.requests
        if request.method == "GET" and urlparse(str(request.url)).path.endswith("/attributes")
    ]


def _rel_gets(fake: _FakeCollibra) -> list[httpx.Request]:
    return [
        request
        for request in fake.requests
        if request.method == "GET" and urlparse(str(request.url)).path.endswith("/relations")
    ]


def test_bearer_auth_header_and_secret_hygiene() -> None:
    fake = _FakeCollibra()
    adapter = _adapter(fake, bearer=True)
    assert adapter.mode == "live"
    assert SECRET_TOKEN not in repr(adapter)
    adapter.create_asset(
        CollibraAssetSpec(
            local_id="db:demo/db",
            name="db",
            display_name="db",
            asset_type_ref=mock_mapping_config().asset_type_refs["database"],
            domain_ref=mock_mapping_config().domain_ref,
            attributes=(
                CollibraAttributeSpec(
                    mock_mapping_config().attribute_type_refs["local_id"],
                    "db:demo/db",
                ),
            ),
        )
    )
    auth_headers = [request.headers.get("Authorization", "") for request in fake.requests]
    assert any(header == f"Bearer {SECRET_TOKEN}" for header in auth_headers)
    for request in fake.requests:
        assert SECRET_TOKEN not in str(request.url)


def test_basic_auth_used_without_bearer_header() -> None:
    fake = _FakeCollibra()
    adapter = _adapter(fake, bearer=False)
    adapter.create_asset(
        CollibraAssetSpec(
            local_id="db:demo/db",
            name="db",
            display_name="db",
            asset_type_ref=mock_mapping_config().asset_type_refs["database"],
            domain_ref=mock_mapping_config().domain_ref,
            attributes=(),
        )
    )
    assert any(
        request.headers.get("Authorization", "").startswith("Basic ") for request in fake.requests
    )


def test_attributes_find_uses_asset_id_and_type_ids_not_type_id() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra(page_size=2)
    fake.assets.append(
        {
            "id": "asset-1",
            "name": "db",
            "domain": {"id": config.domain_ref},
            "type": {"id": config.asset_type_refs["database"]},
        }
    )
    fake.attributes.append(
        {
            "id": "attr-local",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["local_id"]},
            "value": "db:demo/db",
        }
    )
    fake.attributes.append(
        {
            "id": "attr-foreign-type",
            "asset": {"id": "asset-1"},
            "type": {"id": "tenant:unmanaged-type"},
            "value": "ignore-me",
        }
    )
    adapter = _adapter(fake)
    remote = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert len(remote.assets) == 1
    assert all(
        attr.attribute_type_ref != "tenant:unmanaged-type"
        for attr in remote.assets[0].managed_attributes
    )

    for request in _attr_gets(fake):
        query = parse_qs(urlparse(str(request.url)).query)
        assert "typeId" not in query
        assert query.get("assetId") == ["asset-1"]
        assert set(query.get("typeIds", [])) == set(config.attribute_type_refs.values())
        assert "offset" in query and "limit" in query


def test_attributes_find_rejects_global_unscoped_reads() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    for index in range(3):
        fake.assets.append(
            {
                "id": f"asset-{index}",
                "name": f"n-{index}",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["table"]},
            }
        )
        fake.attributes.append(
            {
                "id": f"attr-{index}",
                "asset": {"id": f"asset-{index}"},
                "type": {"id": config.attribute_type_refs["local_id"]},
                "value": f"tbl:local-{index}",
            }
        )
    adapter = _adapter(fake)
    adapter.read_remote_state(CollibraDesiredState(assets=()))
    asset_ids = {
        parse_qs(urlparse(str(request.url)).query)["assetId"][0] for request in _attr_gets(fake)
    }
    assert asset_ids == {"asset-0", "asset-1", "asset-2"}


def test_relations_find_uses_source_id_and_relation_type_id_not_type_id() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    fake.assets.extend(
        [
            {
                "id": "src-1",
                "name": "src",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["schema"]},
            },
            {
                "id": "tgt-1",
                "name": "tgt",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["table"]},
            },
        ]
    )
    fake.attributes.extend(
        [
            {
                "id": "a1",
                "asset": {"id": "src-1"},
                "type": {"id": config.attribute_type_refs["local_id"]},
                "value": "sch:demo",
            },
            {
                "id": "a2",
                "asset": {"id": "tgt-1"},
                "type": {"id": config.attribute_type_refs["local_id"]},
                "value": "tbl:demo",
            },
        ]
    )
    fake.relations.append(
        {
            "id": "rel-1",
            "source": {"id": "src-1"},
            "target": {"id": "tgt-1"},
            "type": {"id": config.relation_type_refs["schema_table"]},
        }
    )
    fake.relations.append(
        {
            "id": "rel-unrelated",
            "source": {"id": "src-1"},
            "target": {"id": "tgt-1"},
            "type": {"id": "tenant:other-relation"},
        }
    )
    adapter = _adapter(fake)
    remote = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert len(remote.relationships) == 1
    assert remote.relationships[0].remote_id == "rel-1"

    for request in _rel_gets(fake):
        query = parse_qs(urlparse(str(request.url)).query)
        assert "typeId" not in query
        assert "sourceId" in query
        assert "relationTypeId" in query
        assert query["sourceId"][0] in {"src-1", "tgt-1"}
        assert query["relationTypeId"][0] in set(config.relation_type_refs.values())


def test_pagination_multiple_pages_and_partial_final_for_assets_and_attributes() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra(page_size=2)
    for index in range(5):
        fake.assets.append(
            {
                "id": f"asset-{index}",
                "name": f"name-{index}",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["table"]},
            }
        )
        fake.attributes.append(
            {
                "id": f"attr-{index}",
                "asset": {"id": f"asset-{index}"},
                "type": {"id": config.attribute_type_refs["local_id"]},
                "value": f"tbl:local-{index}",
            }
        )
    adapter = LiveCollibraAdapter.from_settings(
        _settings(collibra_bearer_token=SECRET_TOKEN),
        config,
        transport=httpx.MockTransport(fake.handler),
    )
    adapter._page_size = 2
    remote = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert len(remote.assets) == 5
    assert [asset.local_id for asset in remote.assets] == sorted(
        asset.local_id for asset in remote.assets
    )


def test_unmanaged_without_local_id_ignored() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    fake.assets.append(
        {
            "id": "unmanaged-1",
            "name": "governance_demo.commerce.customers",
            "domain": {"id": config.domain_ref},
            "type": {"id": config.asset_type_refs["table"]},
        }
    )
    adapter = _adapter(fake)
    remote = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert remote.assets == ()
    assert remote.unmanaged_assets_ignored == 1


def test_patch_attribute_body_includes_matching_id_and_value() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    fake.assets.append(
        {
            "id": "asset-1",
            "name": "governance_demo",
            "displayName": "governance_demo",
            "domain": {"id": config.domain_ref},
            "type": {"id": config.asset_type_refs["database"]},
        }
    )
    fake.attributes.append(
        {
            "id": "attr-owner",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["owner"]},
            "value": "old-owner",
        }
    )
    fake.attributes.append(
        {
            "id": "attr-local",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["local_id"]},
            "value": "db:demo/db",
        }
    )
    fake.attributes.append(
        {
            "id": "attr-custom",
            "asset": {"id": "asset-1"},
            "type": {"id": "tenant:custom-attr"},
            "value": "keep",
        }
    )
    adapter = _adapter(fake)
    adapter.update_asset(
        "asset-1",
        CollibraAssetSpec(
            local_id="db:demo/db",
            name="governance_demo",
            display_name="governance_demo",
            asset_type_ref=config.asset_type_refs["database"],
            domain_ref=config.domain_ref,
            attributes=(
                CollibraAttributeSpec(config.attribute_type_refs["local_id"], "db:demo/db"),
                CollibraAttributeSpec(config.attribute_type_refs["owner"], "new-owner"),
                CollibraAttributeSpec(config.attribute_type_refs["description"], "added"),
            ),
        ),
        patch_name=False,
        patch_display_name=False,
    )
    patch_attr = next(
        request
        for request in fake.requests
        if request.method == "PATCH"
        and urlparse(str(request.url)).path == "/rest/2.0/attributes/attr-owner"
    )
    body = json.loads(patch_attr.content.decode())
    assert body == {"id": "attr-owner", "value": "new-owner"}
    assert not any(
        request.method == "PATCH" and "/assets/" in urlparse(str(request.url)).path
        for request in fake.requests
    )
    assert not any(request.method == "DELETE" for request in fake.requests)
    custom = next(item for item in fake.attributes if item["id"] == "attr-custom")
    assert custom["value"] == "keep"


def test_patch_asset_body_includes_matching_id_and_only_changed_fields() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    fake.assets.append(
        {
            "id": "asset-1",
            "name": "old-name",
            "displayName": "old-display",
            "domain": {"id": config.domain_ref},
            "type": {"id": config.asset_type_refs["database"]},
        }
    )
    fake.attributes.append(
        {
            "id": "attr-local",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["local_id"]},
            "value": "db:demo/db",
        }
    )
    adapter = _adapter(fake)
    adapter.update_asset(
        "asset-1",
        CollibraAssetSpec(
            local_id="db:demo/db",
            name="new-name",
            display_name="new-display",
            asset_type_ref=config.asset_type_refs["database"],
            domain_ref=config.domain_ref,
            attributes=(
                CollibraAttributeSpec(config.attribute_type_refs["local_id"], "db:demo/db"),
            ),
        ),
        patch_name=True,
        patch_display_name=True,
    )
    patch_asset = next(
        request
        for request in fake.requests
        if request.method == "PATCH"
        and urlparse(str(request.url)).path == "/rest/2.0/assets/asset-1"
    )
    body = json.loads(patch_asset.content.decode())
    assert body["id"] == "asset-1"
    assert body["name"] == "new-name"
    assert body["displayName"] == "new-display"


def test_attribute_only_update_skips_asset_patch() -> None:
    config = mock_mapping_config()
    fake = _FakeCollibra()
    fake.assets.append(
        {
            "id": "asset-1",
            "name": "same",
            "displayName": "same",
            "domain": {"id": config.domain_ref},
            "type": {"id": config.asset_type_refs["database"]},
        }
    )
    fake.attributes.append(
        {
            "id": "attr-owner",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["owner"]},
            "value": "old",
        }
    )
    fake.attributes.append(
        {
            "id": "attr-local",
            "asset": {"id": "asset-1"},
            "type": {"id": config.attribute_type_refs["local_id"]},
            "value": "db:x",
        }
    )
    adapter = _adapter(fake)
    adapter.update_asset(
        "asset-1",
        CollibraAssetSpec(
            local_id="db:x",
            name="same",
            display_name="same",
            asset_type_ref=config.asset_type_refs["database"],
            domain_ref=config.domain_ref,
            attributes=(
                CollibraAttributeSpec(config.attribute_type_refs["local_id"], "db:x"),
                CollibraAttributeSpec(config.attribute_type_refs["owner"], "new"),
            ),
        ),
        patch_name=False,
        patch_display_name=False,
    )
    assert not any(
        request.method == "PATCH" and "/assets/" in urlparse(str(request.url)).path
        for request in fake.requests
    )


def test_non_2xx_and_missing_id_are_structured_without_secrets() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "nope", "token": SECRET_TOKEN})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(collibra_bearer_token=SECRET_TOKEN),
        mock_mapping_config(),
        transport=httpx.MockTransport(boom),
    )
    with pytest.raises(CollibraAdapterError) as exc_info:
        adapter.create_asset(
            CollibraAssetSpec(
                local_id="db:x",
                name="x",
                asset_type_ref=mock_mapping_config().asset_type_refs["database"],
                domain_ref=mock_mapping_config().domain_ref,
            )
        )
    assert SECRET_TOKEN not in str(exc_info.value)
    assert exc_info.value.status_code == 500


def test_create_relation_request() -> None:
    fake = _FakeCollibra()
    adapter = _adapter(fake)
    config = mock_mapping_config()
    remote_id = adapter.create_relationship(
        CollibraRelationshipSpec(
            local_key="rel:contains:a->b",
            source_local_id="a",
            target_local_id="b",
            relation_type_ref=config.relation_type_refs["schema_table"],
        ),
        source_remote_id="src-1",
        target_remote_id="tgt-1",
    )
    assert remote_id == "rel-1"
    body = json.loads(fake.requests[-1].content.decode())
    assert body == {
        "sourceId": "src-1",
        "targetId": "tgt-1",
        "typeId": config.relation_type_refs["schema_table"],
    }


def test_factory_mock_default_and_timeout() -> None:
    settings = load_settings(dotenv_path=None, environ={})
    assert settings.collibra_mode == "mock"
    adapter = build_collibra_adapter(settings, mock_mapping_config())
    assert adapter.mode == "mock"
    assert _settings(collibra_bearer_token=SECRET_TOKEN).collibra_timeout_seconds == 10.0


def test_live_requires_single_auth_and_base_url() -> None:
    config = mock_mapping_config()
    with pytest.raises(ValueError, match="exactly one auth"):
        LiveCollibraAdapter.from_settings(
            _settings(
                collibra_username="u",
                collibra_password="p",
                collibra_bearer_token="t",
            ),
            config,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        )
    with pytest.raises(ValueError, match="base_url"):
        LiveCollibraAdapter.from_settings(
            _settings(collibra_base_url="", collibra_bearer_token="t"),
            config,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        )
