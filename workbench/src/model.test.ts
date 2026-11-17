import { describe, expect, it, vi, afterEach } from "vitest";
import fixture from "../../sample/offline-impact/expected-result.json";
import {
  identityKey,
  identityLabel,
  same,
  selectedPaths,
  neighborRelations,
  relationKey,
  selectionForKind,
  reviewAssessment,
  type Review,
  request,
  reportBlob,
  type Identity,
  type Workspace,
  type ImpactResult,
  type GraphNode,
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
const workspace: Workspace = {
  nodes,
  edges: result.impact.affected_edges.map((e) => ({
    ...e,
    source_id: String(identities.findIndex((n) => same(n, e.source))),
    target_id: String(identities.findIndex((n) => same(n, e.target))),
  })),
  result,
  synthetic: true,
  coverage: "test",
  can_analyze: true,
};
afterEach(() => vi.unstubAllGlobals());
describe("evidence presentation preserves identity and recorded paths", () => {
  it("does not merge same-named assets across parents or namespaces", () => {
    const original = identities[0];
    const other = {
      ...original,
      parent: { ...original.parent!, logical_id: "other_schema" },
    };
    expect(same(original, other)).toBe(false);
    expect(same(original, { ...original, namespace: "other" })).toBe(false);
    expect(identityLabel(original)).toContain(
      "offline-demo / demo / analytics / orders",
    );
    expect(identityKey(original)).not.toBe(identityKey(other));
  });
  it("shows only real paths involving the selection, without deriving new lineage", () => {
    expect(selectedPaths(workspace, nodes[1])).toEqual(result.impact.paths);
    expect(selectedPaths(workspace, nodes[2])).toEqual([
      result.impact.paths[1],
    ]);
    const disconnected = {
      ...nodes[0],
      identity: { ...nodes[0].identity, logical_id: "inventory" } as Identity,
    };
    expect(selectedPaths(workspace, disconnected)).toEqual([]);
    expect(result.impact.paths[1].distance).toBe(2);
  });
  it("retains consumer-to-dependency directions and provenance in the relationship view", () => {
    const edges = neighborRelations(workspace, nodes[1]);
    expect(edges).toHaveLength(2);
    expect(edges[0].source.logical_id).toBe("order_totals");
    expect(edges[0].target.logical_id).toBe("orders");
    expect(edges[0].provenance[0].observation_mode).toBe("declared");
    expect(relationKey(edges[0])).toBe(
      relationKey(result.impact.paths[0].steps[0].edge),
    );
  });
  it("does not mutate the result when changing selections", () => {
    const before = JSON.stringify(result);
    nodes.forEach((n) => selectedPaths(workspace, n));
    expect(JSON.stringify(result)).toBe(before);
  });
  it("sends JSON and the local API request header, and surfaces server diagnostics", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Artifact integrity mismatch" }),
    });
    vi.stubGlobal("fetch", fetch);
    await expect(request("result", result)).rejects.toThrow(
      "Artifact integrity mismatch",
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/result",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "X-Workbench-Request": "1" }),
      }),
    );
  });
});

describe("HTML export error boundary", () => {
  it("posts the current engine artifact and returns the server-rendered download", async () => {
    const blob = new Blob(["server-rendered report"], { type: "text/html" });
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, blob: async () => blob });
    vi.stubGlobal("fetch", fetch);
    expect(await reportBlob(result)).toBe(blob);
    expect(fetch).toHaveBeenCalledWith(
      "/api/report",
      expect.objectContaining({ body: JSON.stringify(result) }),
    );
  });
  it("surfaces validation failures and non-JSON server errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ detail: "Content identity mismatch" }),
      }),
    );
    await expect(reportBlob(result)).rejects.toThrow(
      "Content identity mismatch",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => {
          throw new Error("not JSON");
        },
      }),
    );
    await expect(reportBlob(result)).rejects.toThrow("HTML export failed");
  });
});

it("explains an unavailable backend during export without modifying the artifact", async () => {
  const before = JSON.stringify(result);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );
  await expect(reportBlob(result)).rejects.toThrow(
    "Cannot reach the local API",
  );
  expect(JSON.stringify(result)).toBe(before);
});


describe("post-audit selection and assessment semantics", () => {
  it("preserves an explicit dataset filter while replacing an incompatible selection", () => {
    const dataset = { ...nodes[0], id: "dataset", identity: { ...nodes[0].identity, kind: "dataset" } };
    expect(selectionForKind([...nodes, dataset], nodes[1].id, "dataset")).toBe("dataset");
    expect(selectionForKind([...nodes, dataset], dataset.id, "table")).toBe(nodes[0].id);
    expect(selectionForKind(nodes, nodes[1].id, "all")).toBe(nodes[1].id);
    expect(selectionForKind(nodes, nodes[1].id, "column")).toBe("");
  });
  it.each([
    ["not_run", false, "Not assessed"],
    ["unknown", false, "Unknown"],
    ["clear", true, "no conflict findings"],
    ["findings", true, "findings recorded"],
    ["blocked", true, "blockers recorded"],
  ])("distinguishes assessment state %s", (status, assessed, title) => {
    const review = { conflicts: { status, conflict_report: { results: [] } } } as unknown as Review;
    const display = reviewAssessment(review);
    expect(display.assessed).toBe(assessed);
    expect(display.title).toContain(title);
  });
  it("does not infer successful assessment from counts without a report", () => {
    const review = { conflicts: { status: "clear", summary: { unresolved_conflict: 0 }, conflict_report: null } } as unknown as Review;
    expect(reviewAssessment(review).assessed).toBe(false);
    expect(reviewAssessment(review).title).toContain("Insufficient");
  });
});
