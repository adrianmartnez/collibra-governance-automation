export type Identity = {
  namespace: string;
  kind: string;
  logical_id: string;
  parent: Identity | null;
};
export type Provenance = {
  provider_type: string;
  source_ref: string;
  source_version: string | null;
  observation_mode: string;
};
export type Relation = {
  source: Identity;
  target: Identity;
  kind: string;
  attributes: Record<string, unknown>;
  provenance: Provenance[];
  source_id: string;
  target_id: string;
};
export type GraphNode = {
  id: string;
  identity: Identity;
  name: string;
  description: string | null;
  attributes: Record<string, unknown>;
  provenance: Provenance[];
  impact_class: string;
  distance: number | null;
};
export type ImpactPath = {
  root: Identity;
  target: Identity;
  distance: number;
  steps: {
    from_node: Identity;
    to_node: Identity;
    traversal: string;
    edge: Omit<Relation, "source_id" | "target_id">;
  }[];
};
export type Policy = {
  policy_id: string;
  severity: string;
  rule_type: string;
  matched_objects: { object_kind: string; object_id: string; node: Identity }[];
};
export type ImpactResult = {
  status: string;
  impact_detected: boolean;
  writes_performed: number;
  content_identity: { digest: string };
  graph_identity: { digest: string };
  policy_identity: unknown;
  affected_policies: Policy[];
  impact: {
    changed_nodes: Identity[];
    direct_nodes: Identity[];
    transitive_nodes: Identity[];
    paths: ImpactPath[];
    affected_edges: Relation[];
  };
};
export type Workspace = {
  nodes: GraphNode[];
  edges: Relation[];
  result: ImpactResult | null;
  synthetic: boolean;
  coverage: string;
  can_analyze: boolean;
  manifest?: Record<string, unknown>;
  namespace?: string;
};
export type Plan = {
  plan_version: string;
  content_identity: { digest: string };
  target_context: { provider: string; mode: string };
  actions: {
    action_type: string;
    object_kind: string;
    local_id: string | null;
    remote_id: string | null;
    reason: string;
    changed_fields: string[];
    desired_asset: unknown;
    desired_relationship: unknown;
  }[];
  reconciliation_assumptions?: {
    actions: {
      local_id: string;
      action_type: string;
      properties: {
        object: Identity;
        property: string;
        roles: string[];
        decision: { state: string; reason: string } | null;
      }[];
    }[];
  };
};
export type Review = {
  status: string;
  content_identity: { digest: string };
  conflicts: {
    status: string;
    summary: Record<string, number>;
    conflict_report: {
      results: {
        object: Identity;
        property: string;
        state: string;
        reason: string;
        value_groups: unknown[];
      }[];
    } | null;
    reconciliation_assessments: unknown[];
  };
  drift: { status: string; summary: unknown };
};
export const identityKey = (n: Identity): string =>
  JSON.stringify([
    n.namespace,
    n.kind,
    n.logical_id,
    n.parent ? identityKey(n.parent) : null,
  ]);
export function identityLabel(n: Identity): string {
  return n.parent
    ? `${identityLabel(n.parent)} / ${n.logical_id}`
    : `${n.namespace} / ${n.logical_id}`;
}
export const same = (a: Identity, b: Identity) =>
  identityKey(a) === identityKey(b);
export function selectedPaths(w: Workspace, n: GraphNode) {
  return (
    w.result?.impact.paths.filter(
      (p) =>
        same(p.root, n.identity) ||
        same(p.target, n.identity) ||
        p.steps.some(
          (s) => same(s.from_node, n.identity) || same(s.to_node, n.identity),
        ),
    ) ?? []
  );
}
export function neighborRelations(w: Workspace, n: GraphNode) {
  return w.edges.filter((e) => e.source_id === n.id || e.target_id === n.id);
}
export const relationKey = (e: Pick<Relation, "source" | "target" | "kind">) =>
  `${identityKey(e.source)}|${e.kind}|${identityKey(e.target)}`;
export async function request<T>(endpoint: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/${endpoint}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Workbench-Request": "1" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Local API unavailable." }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "The artifact could not be validated.",
    );
  }
  return response.json() as Promise<T>;
}

export async function reportBlob(result: ImpactResult): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch("/api/report", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Workbench-Request": "1",
      },
      body: JSON.stringify(result),
    });
  } catch {
    throw new Error(
      "Cannot reach the local API. Start the server on 127.0.0.1:8765 and retry the export. Your workspace is unchanged.",
    );
  }
  if (!response.ok) {
    const failure = await response.json().catch(() => ({
      detail:
        "HTML export failed. Check that the local API is running and retry.",
    }));
    throw new Error(
      typeof failure.detail === "string"
        ? failure.detail
        : "HTML export failed. The current workspace has been preserved.",
    );
  }
  return response.blob();
}

/** Keep explicit type filters authoritative when selection moves to another kind. */
export function selectionForKind(
  nodes: GraphNode[], selectedId: string, kind: string,
): string {
  const current = nodes.find(n => n.id === selectedId);
  if (current && (kind === "all" || current.identity.kind === kind)) return selectedId;
  return nodes.find(n => kind === "all" || n.identity.kind === kind)?.id ?? "";
}

/** Describe recorded assessment state without converting missing evidence to success. */
export function reviewAssessment(review: Review) {
  const lane = review.conflicts;
  if (lane.status === "not_run") return {
    assessed: false,
    title: "Not assessed — review not run",
    detail: "No conflict assessment was executed. Evidence is insufficient to conclude agreement, compliance or absence of conflicts.",
  };
  if (!["clear", "findings", "blocked"].includes(lane.status)) return {
    assessed: false,
    title: "Unknown assessment state",
    detail: "The recorded state is unknown. No successful assessment is inferred.",
  };
  if (!lane.conflict_report) return {
    assessed: false,
    title: "Insufficient assessment evidence",
    detail: "The recorded lane has no conflict report. Its outcome cannot be interpreted from summary counts alone.",
  };
  if (lane.status === "clear") return {
    assessed: true,
    title: "Assessed — no conflict findings recorded",
    detail: "The supplied assessment recorded no conflict findings. This is not a completeness or compliance guarantee.",
  };
  return {
    assessed: true,
    title: lane.status === "blocked" ? "Assessed — blockers recorded" : "Assessed — findings recorded",
    detail: "Inspect the recorded findings and reasons. A finding is not automatically a policy violation.",
  };
}
