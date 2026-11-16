"""Evidence fidelity, offline behavior and untrusted HTML boundary tests."""

from __future__ import annotations

import copy
import json
import socket
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import httpx
import psycopg
import pytest

from governance.identity import impact_result_identity
from governance.impact import (
    ImpactIntegrityError,
    ImpactParseError,
    ImpactSchemaError,
    UnsupportedImpactVersionError,
    load_impact_result,
)
from governance.impact.html import render_impact_report, render_impact_report_from_file

DEMO = Path(__file__).resolve().parents[1] / "sample/offline-impact/expected-result.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Renderer attempted external access")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr(psycopg, "connect", denied)
    monkeypatch.setattr(psycopg.Connection, "connect", denied)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", denied)


def resign(result):
    result["content_identity"] = impact_result_identity(
        {k: v for k, v in result.items() if k != "content_identity"}
    ).to_dict()
    return result


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_impacted_fidelity_determinism_and_no_mutation(tmp_path):
    result = load_impact_result(DEMO)
    original = copy.deepcopy(result)
    report = render_impact_report(result)
    assert report == render_impact_report_from_file(DEMO) == render_impact_report(result)
    assert result == original
    assert list(tmp_path.iterdir()) == []
    assert "Impact identified" in report
    assert "Direct impacts (1)" in report
    assert "Transitive impacts (1)" in report
    assert "Changed assets (1)" in report
    for name in ("orders", "order_totals", "revenue_report"):
        assert f"offline-demo:table:{name}" in report
    assert "inventory" not in report  # Not part of the result artifact.
    for path in result["impact"]["paths"]:
        assert f"Recorded distance: {path['distance']}" in report
        for step in path["steps"]:
            assert f"Traversal: {step['traversal']}" in report
            for prov in step["edge"]["provenance"]:
                assert prov["source_ref"] in report
                assert prov["observation_mode"] in report
    assert result["content_identity"]["digest"] in report
    assert "writes_performed: 0" in report
    assert "policy compliance is unknown" in report


def test_clear_is_not_a_completeness_claim():
    result = load_impact_result(DEMO)
    result["status"] = "clear"
    result["impact_detected"] = False
    for field in ("direct_nodes", "transitive_nodes", "affected_edges", "paths"):
        result["impact"][field] = []
    report = render_impact_report(resign(result))
    assert "No impact identified in the supplied graph" in report
    assert "Direct impacts (0)" in report
    assert "Transitive impacts (0)" in report
    assert "No affected relationships recorded" in report
    assert "No paths recorded" in report
    assert "Empty lists do not establish complete coverage" in report
    assert "model.demo.revenue_report" not in report


def test_missing_evidence_is_separate_from_engine_status():
    result = load_impact_result(DEMO)
    result["impact"]["changed_nodes"] = []
    result["impact"]["paths"] = []
    result["impact"]["affected_edges"][0]["provenance"] = []
    report = render_impact_report(resign(result))
    assert "Artifact status: impacted" in report
    assert "Insufficient evidence: no changed assets recorded" in report
    assert "some listed impacts have no recorded path" in report
    assert "relationship provenance is not recorded" in report


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        '</pre><img src="https://evil.invalid/x" onerror="alert(1)">',
        '" autofocus onfocus=alert(1) & <svg/onload=alert(1)>',
        "javascript:alert(1)",
        '</style><iframe srcdoc="<script>alert(1)</script>"></iframe>',
    ],
)
def test_untrusted_metadata_remains_inert_text(payload):
    result = load_impact_result(DEMO)

    def replace(value):
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, dict):
            return {
                k: payload
                if k in {"namespace", "logical_id", "provider_type", "source_ref", "source_version"}
                else replace(v)
                for k, v in value.items()
            }
        return value

    result = replace(result)
    result["impact"]["affected_edges"][0]["kind"] = payload
    result["impact"]["affected_edges"][0]["attributes"] = {payload: payload}
    result["affected_policies"] = [
        {
            "policy_id": payload,
            "severity": "warning",
            "rule_type": "require_owner",
            "matched_objects": [
                {
                    "object_kind": "table",
                    "object_id": payload,
                    "node": result["impact"]["changed_nodes"][0],
                }
            ],
        }
    ]
    report = render_impact_report(resign(result))
    assert escape(payload, quote=True) in report
    document = Document(report)
    allowed = {
        "html",
        "head",
        "meta",
        "title",
        "style",
        "body",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "section",
        "p",
        "strong",
        "ul",
        "ol",
        "li",
        "pre",
    }
    assert {tag for tag, _ in document.tags} <= allowed
    for _, attrs in document.tags:
        assert not any(k.startswith("on") or k in {"src", "href", "srcdoc"} for k in attrs)
    assert any(payload in text for text in document.text)


def test_no_remote_resources_or_active_content():
    report = render_impact_report_from_file(DEMO)
    document = Document(report)
    assert not {"script", "link", "img", "iframe", "object", "embed", "form"} & {
        tag for tag, _ in document.tags
    }
    assert all(not {"href", "src", "srcset"} & attrs.keys() for _, attrs in document.tags)
    assert "url(" not in report and "@import" not in report
    assert "default-src 'none'" in report


@pytest.mark.parametrize(
    "kind,exception",
    [
        ("version", UnsupportedImpactVersionError),
        ("schema", ImpactSchemaError),
        ("missing", ImpactSchemaError),
        ("tampered", ImpactIntegrityError),
    ],
)
def test_rejects_invalid_documents_for_both_entrypoints(tmp_path, kind, exception):
    result = load_impact_result(DEMO)
    if kind == "version":
        result["result_version"] = "2"
    elif kind == "schema":
        result["result_schema"] = "other"
    elif kind == "missing":
        del result["impact"]
    else:
        result["status"] = "clear"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(exception):
        render_impact_report(result)
    with pytest.raises(exception):
        render_impact_report_from_file(path)


def test_malformed_json_fails_explicitly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ImpactParseError):
        render_impact_report_from_file(path)
