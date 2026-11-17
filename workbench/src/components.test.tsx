import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, it, expect } from "vitest";
import fixture from "../../sample/offline-impact/expected-result.json";
import { Relations, Inspector } from "./components";
import {
  type ImpactResult,
  type Workspace,
  type GraphNode,
  relationKey,
  same,
} from "./model";
const result = fixture as unknown as ImpactResult;
const identities = [
  ...result.impact.changed_nodes,
  ...result.impact.direct_nodes,
  ...result.impact.transitive_nodes,
];
const nodes: GraphNode[] = identities.map((identity, i) => ({
  id: String(i),
  name: identity.logical_id,
  identity,
  description: null,
  attributes: {},
  provenance: [],
  impact_class: ["Changed", "Direct", "Transitive"][i],
  distance: i || null,
}));
const edges = result.impact.affected_edges.map((e) => ({
  ...e,
  source_id: String(identities.findIndex((n) => same(n, e.source))),
  target_id: String(identities.findIndex((n) => same(n, e.target))),
}));
const w: Workspace = {
  nodes,
  edges,
  result,
  synthetic: true,
  coverage: "test",
  can_analyze: true,
};
const noop = () => {};
describe("contextual evidence components", () => {
  it("separates containment from actual impact dependencies and retains stored direction", () => {
    const containment = { ...edges[0], kind: "contains" };
    const html = renderToStaticMarkup(
      createElement(Relations, {
        edges: [...edges, containment],
        selected: nodes[1],
        focused: null,
        onFocus: noop,
        onSelect: noop,
      }),
    );
    expect(html).toContain("Impact dependencies");
    expect(html).toContain("Hierarchy &amp; other relationships");
    expect(html).toContain("not a consumer dependency");
    expect(html).toContain("Upstream dependency");
    expect(html).toContain("Downstream consumer");
  });
  it("exposes the same focused relationship to the row and graph view", () => {
    const html = renderToStaticMarkup(
      createElement(Relations, {
        edges,
        selected: nodes[1],
        focused: relationKey(edges[0]),
        onFocus: noop,
        onSelect: noop,
      }),
    );
    expect(html.match(/relation-focused/g)).toHaveLength(1);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("Clear focus");
  });
  it("shows an understandable recorded path and navigable neighbors without JSON", () => {
    const html = renderToStaticMarkup(
      createElement(Inspector, {
        node: nodes[1],
        workspace: w,
        busy: false,
        onAnalyze: noop,
        onExport: noop,
        onPolicies: noop,
        onSelect: noop,
      }),
    );
    expect(html).toContain("Recorded route to selection");
    expect(html).toContain("orders");
    expect(html).toContain("order_totals");
    expect(html).toContain("revenue_report");
    expect(html).toContain("1 hop");
    expect(html).toContain("Relationship provenance (2)");
  });
});


it("public inspector exposes only the real static report action", () => {
  const html = renderToStaticMarkup(createElement(Inspector, {
    node: nodes[1], workspace: { ...w, can_analyze: false }, busy: false,
    onAnalyze: noop, onExport: noop, onPolicies: noop, onSelect: noop, publicDemo: true,
  }));
  expect(html).toContain("Download scenario report");
  expect(html).not.toContain("Analyze selected");
  expect(html).not.toContain("Review policy evidence");
});
