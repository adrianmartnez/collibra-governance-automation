import {
  useEffect,
  useMemo,
  useRef,
  type RefObject,
  type ReactNode,
} from "react";
import {
  ReactFlow,
  useReactFlow,
  getNodesBounds,
  getViewportForBounds,
  Controls,
  Background,
  Handle,
  Position,
  MarkerType,
  type Node,
  type NodeProps,
  type Edge,
} from "@xyflow/react";
import {
  ArrowDownToLine,
  ArrowRight,
  Box,
  Check,
  GitBranch,
  Info,
  Layers3,
  Play,
  Shield,
  Table2,
} from "lucide-react";
import {
  type Workspace,
  type GraphNode,
  type ImpactPath,
  type Relation,
  identityLabel,
  same,
  selectedPaths,
  neighborRelations,
  relationKey,
} from "./model";
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Empty({
  icon = <Layers3 />,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function JsonDetails({
  title,
  value,
}: {
  title: string;
  value: unknown;
}) {
  return (
    <details className="technical">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function Prov({ records }: { records: Relation["provenance"] }) {
  return records.length ? (
    <div className="provenance">
      {records.map((p, i) => (
        <div key={i}>
          <Badge tone="rose">{p.observation_mode}</Badge>
          <strong>{p.provider_type}</strong>
          <span>{p.source_ref}</span>
          <small>Source version: {p.source_version ?? "Not recorded"}</small>
        </div>
      ))}
    </div>
  ) : (
    <p className="muted">
      No relationship provenance recorded. Evidence is insufficient to identify
      its source.
    </p>
  );
}
export function PathCards({ paths }: { paths: ImpactPath[] }) {
  return paths.length ? (
    <div className="path-list">
      {paths.map((p, i) => (
        <article className="path-card" key={i}>
          <div>
            <GitBranch size={16} />
            <strong>{p.root.logical_id}</strong>
            <ArrowRight size={14} />
            <strong>{p.target.logical_id}</strong>
            <Badge>
              {p.distance} {p.distance === 1 ? "hop" : "hops"}
            </Badge>
          </div>
          <ol>
            {p.steps.map((step, j) => (
              <li key={j}>
                <span>
                  {step.from_node.logical_id} <ArrowRight size={12} />{" "}
                  {step.to_node.logical_id}
                </span>
                <small>
                  {step.edge.kind} · {step.traversal} traversal
                </small>
                <Prov records={step.edge.provenance} />
              </li>
            ))}
          </ol>
        </article>
      ))}
    </div>
  ) : (
    <Empty title="No recorded paths">
      No explanation path is present for this selection. This is not proof of
      complete lineage coverage.
    </Empty>
  );
}
export function Relations({
  edges,
  selected,
  focused,
  onFocus,
  onSelect,
}: {
  edges: Relation[];
  selected?: GraphNode;
  focused: string | null;
  onFocus: (key: string | null) => void;
  onSelect: (id: string) => void;
}) {
  if (!edges.length)
    return (
      <Empty title="No recorded relationships">
        No relationships for this selection are present in the loaded evidence.
      </Empty>
    );
  return (
    <>
      {[
        { title: "Impact dependencies", kind: true },
        { title: "Hierarchy & other relationships", kind: false },
      ].map((group) => {
        const rows = edges.filter(
          (e) => (e.kind === "depends_on") === group.kind,
        );
        return (
          rows.length > 0 && (
            <section className="relationship-group" key={group.title}>
              <h3>
                {group.title} <Badge>{rows.length}</Badge>
              </h3>
              {!group.kind && (
                <p className="muted">
                  Containment describes structure, not a consumer dependency.
                  These rows are not additional downstream impacts.
                </p>
              )}
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Stored relationship</th>
                      <th>Direction / kind</th>
                      <th>Provenance</th>
                      <th>Graph</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((e) => (
                      <tr
                        key={relationKey(e)}
                        className={
                          focused === relationKey(e) ? "relation-focused" : ""
                        }
                      >
                        <td>
                          <button
                            className="text-button"
                            onClick={() => onSelect(e.source_id)}
                            title={identityLabel(e.source)}
                          >
                            {e.source.logical_id}
                          </button>
                          <ArrowRight size={14} />
                          <button
                            className="text-button"
                            onClick={() => onSelect(e.target_id)}
                            title={identityLabel(e.target)}
                          >
                            {e.target.logical_id}
                          </button>
                        </td>
                        <td>
                          {group.kind
                            ? selected
                              ? e.source_id === selected.id
                                ? "Upstream dependency"
                                : "Downstream consumer"
                              : "Consumer → dependency"
                            : "Structural relationship"}
                          <small>{e.kind}</small>
                        </td>
                        <td>
                          {e.provenance.length ? (
                            e.provenance.map((p, j) => (
                              <Badge key={j}>{p.observation_mode}</Badge>
                            ))
                          ) : (
                            <Badge>Not recorded</Badge>
                          )}
                          <details>
                            <summary>View evidence</summary>
                            <Prov records={e.provenance} />
                            <JsonDetails
                              title="Relationship attributes"
                              value={e.attributes}
                            />
                          </details>
                        </td>
                        <td>
                          <button
                            aria-pressed={focused === relationKey(e)}
                            className="relation-focus"
                            onClick={() =>
                              onFocus(
                                focused === relationKey(e)
                                  ? null
                                  : relationKey(e),
                              )
                            }
                          >
                            {focused === relationKey(e)
                              ? "Clear focus"
                              : "Focus edge"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )
        );
      })}
    </>
  );
}
function AutoFit({
  layoutKey,
  container,
}: {
  layoutKey: string;
  container: RefObject<HTMLDivElement | null>;
}) {
  const { getNodes, setViewport, viewportInitialized } = useReactFlow();
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const fit = () => {
      if (!element.clientWidth || !element.clientHeight) return;
      const rendered = new Map(
        [...element.querySelectorAll<HTMLElement>(".react-flow__node")]
          .map(node => [node.getAttribute("data-id"), node] as const),
      );
      const nodes = getNodes().flatMap(node => {
        const measured = rendered.get(node.id);
        if (!measured?.offsetWidth || !measured.offsetHeight) return [];
        return [{ ...node, measured: { width: measured.offsetWidth, height: measured.offsetHeight } }];
      });
      if (!nodes.length) return;
      // Use layout pixels, not transformed rectangles or a throttled animation frame.
      void setViewport(getViewportForBounds(
        getNodesBounds(nodes), element.clientWidth, element.clientHeight, 0.15, 1.1, 0.13,
      ));
    };
    const observer = new ResizeObserver(fit);
    observer.observe(element);
    element.querySelectorAll(".react-flow__node").forEach(node => observer.observe(node));
    window.addEventListener("resize", fit);
    fit();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, [viewportInitialized, layoutKey, container, getNodes, setViewport]);
  return null;
}

type VisualNode = Node<{ asset: GraphNode; disconnected: boolean }, "asset">;
function AssetNode({ data, selected }: NodeProps<VisualNode>) {
  return (
    <div
      className={`asset-node ${data.asset.impact_class.toLowerCase().replace(" ", "-")} ${selected ? "selected" : ""} ${data.disconnected ? "disconnected" : ""}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <Table2 size={20} />
      <div>
        <strong title={data.asset.name}>{data.asset.name}</strong>
        <small>
          {data.asset.identity.kind.replace("_", " ")} ·{" "}
          {data.asset.impact_class}
        </small>
      </div>
      {data.disconnected && (
        <Info size={15} aria-label="No recorded dependency edges" />
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { asset: AssetNode };
export function Graph({
  workspace,
  selected,
  visible,
  onSelect,
  focused,
  onFocus,
}: {
  focused: string | null;
  onFocus: (key: string | null) => void;
  workspace: Workspace;
  selected: GraphNode | undefined;
  visible: GraphNode[];
  onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const data = useMemo(() => {
    const ids = new Set(visible.map((n) => n.id));
    const columns = new Map<number, number>();
    const paths = selected ? selectedPaths(workspace, selected) : [];
    const highlighted = new Set(
      paths.flatMap((p) => p.steps.map((s) => relationKey(s.edge))),
    );
    if (selected)
      neighborRelations(workspace, selected)
        .filter((e) => e.kind === "depends_on")
        .forEach((e) => highlighted.add(relationKey(e)));
    const ordered = [...visible].sort((a, b) => {
      const rank = (n: GraphNode) =>
        n.impact_class === "Changed" ? 0 : (n.distance ?? 999);
      return rank(a) - rank(b) || a.name.localeCompare(b.name);
    });
    let unplaced = 0;
    const nodes: VisualNode[] = ordered.map((n, i) => {
      const analyzed = n.impact_class === "Changed" || n.distance !== null;
      const column = workspace.result
        ? analyzed
          ? n.impact_class === "Changed"
            ? 0
            : (n.distance ?? 0)
          : (1 + unplaced++) % 3
        : i % 3;
      const row = columns.get(column) ?? 0;
      const y =
        workspace.result && !analyzed
          ? Math.max(1, ...columns.values()) * 125
          : row * 125;
      columns.set(column, Math.floor(y / 125) + 1);
      const disconnected = !workspace.edges.some(
        (e) =>
          e.kind === "depends_on" &&
          (e.source_id === n.id || e.target_id === n.id),
      );
      return {
        id: n.id,
        type: "asset",
        position: { x: column * 250, y },
        ariaLabel: `Select ${n.name}: ${n.impact_class}`,
        selected: selected?.id === n.id,
        data: { asset: n, disconnected },
      };
    });
    const edges: Edge[] = workspace.edges
      .filter((e) => ids.has(e.source_id) && ids.has(e.target_id))
      .map((e, i) => ({
        id: String(i),
        source: e.kind === "depends_on" ? e.target_id : e.source_id,
        target: e.kind === "depends_on" ? e.source_id : e.target_id,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        data: { key: relationKey(e) },
        className:
          focused === relationKey(e)
            ? "edge-focused"
            : highlighted.has(relationKey(e))
              ? "path-highlight"
              : "",
        style: {
          stroke:
            focused === relationKey(e) || highlighted.has(relationKey(e))
              ? "var(--accent)"
              : "var(--edge)",
          strokeWidth:
            focused === relationKey(e)
              ? 4
              : highlighted.has(relationKey(e))
                ? 2.4
                : 1.4,
        },
        label: e.kind === "depends_on" ? undefined : e.kind,
      }));
    return { nodes, edges };
  }, [workspace, selected, visible, focused]);
  return (
    <div
      ref={container}
      className="graph-canvas"
      aria-label="Interactive lineage graph"
      onKeyDownCapture={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const element = (event.target as HTMLElement).closest(".react-flow__node");
        const id = element?.getAttribute("data-id");
        if (!id || !visible.some(n => n.id === id)) return;
        event.preventDefault();
        event.stopPropagation();
        onSelect(id);
      }}
    >
      <ReactFlow
        nodes={data.nodes}
        edges={data.edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        onEdgeClick={(_, e) => onFocus(String(e.data?.key))}
        edgesFocusable
        nodesFocusable
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        minZoom={0.15}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <AutoFit
          container={container}
          layoutKey={data.nodes
            .map((n) => `${n.id}:${n.position.x}:${n.position.y}`)
            .join("|")}
        />
        <Background color="#232326" gap={22} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
export function Inspector({
  node,
  workspace,
  busy,
  onAnalyze,
  onExport,
  onPolicies,
  onSelect,
  publicDemo = false,
}: {
  publicDemo?: boolean;
  node: GraphNode | undefined;
  workspace: Workspace;
  busy: boolean;
  onAnalyze: () => void;
  onExport: () => void;
  onPolicies: () => void;
  onSelect: (id: string) => void;
}) {
  if (!node)
    return (
      <aside className="panel inspector">
        <Empty icon={<Box />} title="Select an asset">
          Choose a graph node to inspect its relationships and recorded impact.
        </Empty>
      </aside>
    );
  const edges = neighborRelations(workspace, node);
  const upstream = edges.filter(
    (e) => e.kind === "depends_on" && e.source_id === node.id,
  );
  const downstream = edges.filter(
    (e) => e.kind === "depends_on" && e.target_id === node.id,
  );
  const path = workspace.result?.impact.paths.find((p) =>
    same(p.target, node.identity),
  );
  const matches = workspace.result?.affected_policies.filter((p) =>
    p.matched_objects.some((o) => same(o.node, node.identity)),
  );
  return (
    <aside className="panel inspector">
      <div className="eyebrow">Selected asset</div>
      <div className="inspector-title">
        <Table2 />
        <h2>{node.name}</h2>
      </div>
      <p className="identity">{identityLabel(node.identity)}</p>
      <div className="badge-row">
        <Badge
          tone={
            node.impact_class === "Changed"
              ? "rose"
              : node.impact_class === "Direct"
                ? "rose"
                : "neutral"
          }
        >
          {node.impact_class}
        </Badge>
        <Badge>{node.identity.kind.replace("_", " ")}</Badge>
      </div>
      <dl className="facts">
        <div>
          <dt>Upstream dependencies</dt>
          <dd>{upstream.length}</dd>
        </div>
        <div>
          <dt>Downstream consumers</dt>
          <dd>{downstream.length}</dd>
        </div>
        <div>
          <dt>Recorded impact distance</dt>
          <dd>{node.distance ?? "—"}</dd>
        </div>
        <div>
          <dt>Matching policies</dt>
          <dd>
            {workspace.result?.policy_identity
              ? (matches?.length ?? 0)
              : "Not supplied"}
          </dd>
        </div>
      </dl>
      <div className="inspector-section">
        <div className="eyebrow">Evidence summary</div>
        {node.impact_class === "Changed" ? (
          <p>
            <Check size={16} /> Explicit hypothetical change
          </p>
        ) : path ? (
          <p>
            <Check size={16} /> {path.distance} recorded{" "}
            {path.distance === 1 ? "hop" : "hops"} from {path.root.logical_id}
          </p>
        ) : (
          <p>
            <Info size={16} /> No path to this asset recorded
          </p>
        )}
        <p>
          <GitBranch size={16} />{" "}
          {edges.filter((e) => e.kind === "depends_on").length} recorded
          dependency relationships
        </p>
        {edges.length === 0 && (
          <p className="muted">
            No relationships recorded in this view. Coverage outside it is
            unknown.
          </p>
        )}
      </div>
      <section className="inspector-section">
        <div className="eyebrow">Recorded route to selection</div>
        {path ? (
          <>
            <p className="route-chain">
              {[path.root, ...path.steps.map((s) => s.to_node)].map((n, i) => (
                <span key={i}>
                  {i > 0 && <ArrowRight size={12} />}
                  <strong>{n.logical_id}</strong>
                </span>
              ))}
            </p>
            <small>
              {path.distance} {path.distance === 1 ? "hop" : "hops"} · supplied
              engine result
            </small>
          </>
        ) : (
          <p>
            {node.impact_class === "Changed"
              ? "Selected change root; no downstream hop required."
              : "No impact route recorded for this asset."}
          </p>
        )}
        <div className="neighbor-links">
          {[
            { title: "Upstream", items: upstream },
            { title: "Downstream", items: downstream },
          ].map((group) => (
            <div key={group.title}>
              <small>{group.title}</small>
              {group.items.length ? (
                group.items.map((e) => {
                  const id =
                    group.title === "Upstream" ? e.target_id : e.source_id;
                  const target = workspace.nodes.find((n) => n.id === id);
                  return (
                    target && (
                      <button key={relationKey(e)} onClick={() => onSelect(id)}>
                        {target.name}
                        <ArrowRight size={12} />
                      </button>
                    )
                  );
                })
              ) : (
                <span>None recorded</span>
              )}
            </div>
          ))}
        </div>
      </section>
      <details className="inspector-section">
        <summary>
          Relationship provenance ({upstream.length + downstream.length})
        </summary>
        {[...upstream, ...downstream].map((e) => (
          <div key={relationKey(e)}>
            <p>
              {e.source.logical_id} → {e.target.logical_id}
            </p>
            <Prov records={e.provenance} />
          </div>
        ))}
        {!upstream.length && !downstream.length && (
          <p>No dependency provenance is available for this selection.</p>
        )}
      </details>
      {node.description && <p className="muted">{node.description}</p>}
      <details className="inspector-section">
        <summary>Identity & source metadata</summary>
        <p>Namespace: {node.identity.namespace}</p>
        <p>Kind: {node.identity.kind}</p>
        <Prov records={node.provenance} />
        <JsonDetails
          title="Raw identity & attributes"
          value={{ identity: node.identity, attributes: node.attributes }}
        />
      </details>
      <div className="inspector-actions">
        {!publicDemo && <button
          className="primary"
          disabled={busy || !workspace.can_analyze}
          onClick={onAnalyze}
          title={
            workspace.can_analyze
              ? "Run the existing Python engine with this node as the hypothetical change"
              : "Result artifacts cannot be reanalyzed without a full dbt manifest"
          }
        >
          <Play size={16} />
          Analyze selected
        </button>}
        <button disabled={busy || !workspace.result} onClick={onExport}>
          <ArrowDownToLine size={16} />
          {publicDemo ? "Download scenario report" : "Export HTML report"}
        </button>
        {!publicDemo && <button onClick={onPolicies}>
          <Shield size={16} />
          Review policy evidence
        </button>}
      </div>
      <p className="footnote">
        Read-only analysis. No asset is removed or synchronized. Unrecorded
        dependencies remain unknown.
      </p>
    </aside>
  );
}
