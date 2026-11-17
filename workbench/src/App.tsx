import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { ReactFlowProvider } from "@xyflow/react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronRight,
  Database,
  FileCheck2,
  FileJson,
  FolderOpen,
  GitBranch,
  Info,
  Layers3,
  LoaderCircle,
  Play,
  Search,
  Settings2,
  Shield,
  Table2,
  TriangleAlert,
  Upload,
  X,
  Zap,
} from "lucide-react";
import {
  type Workspace,
  type Plan,
  type Review,
  identityLabel,
  selectedPaths,
  neighborRelations,
  relationKey,
  selectionForKind,
  reviewAssessment,
} from "./model";
import {
  Badge,
  Empty,
  JsonDetails,
  PathCards,
  Relations,
  Graph,
  Inspector,
} from "./components";
import { runtime } from "./runtime";
import { PublicWelcome } from "./PublicWelcome";
const publicMode = runtime.kind === "public";
const request = runtime.request;
type Page = "Dashboard" | "Lineage" | "Impact Analysis" | "Policies" | "Plans";
type ImportKind = "graph" | "result" | "review" | "plan";
const allPages: Page[] = [
  "Dashboard",
  "Lineage",
  "Impact Analysis",
  "Policies",
  "Plans",
];
const pages = publicMode ? allPages.slice(0, 3) : allPages;
export default function App() {
  const [page, setPage] = useState<Page>(
    () =>
      pages.find(
        (p) => p.replaceAll(" ", "-").toLowerCase() === location.hash.slice(1),
      ) ?? "Impact Analysis",
  );
  const [activeScenario, setActiveScenario] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [focusedRelation, setFocusedRelation] = useState<string | null>(null);
  const selectNode = (id: string) => {
    const target = workspace?.nodes.find((n) => n.id === id);
    if (target && kind !== "all" && target.identity.kind !== kind)
      setKind("all");
    setSelectedId(id);
    setFocusedRelation(null);
  };
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [online, setOnline] = useState(false);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("table");
  const [classification, setClassification] = useState("all");
  const [tab, setTab] = useState("dependencies");
  const [importing, setImporting] = useState(false);
  const [about, setAbout] = useState(false);
  const [importKind, setImportKind] = useState<ImportKind>("graph");
  const [namespace, setNamespace] = useState("offline-demo");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!publicMode) request("health")
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
    const listener = () =>
      setPage(
        pages.find(
          (p) =>
            p.replaceAll(" ", "-").toLowerCase() === location.hash.slice(1),
        ) ?? "Dashboard",
      );
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    if (!importing && !about) return;
    const previous = document.activeElement as HTMLElement | null;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        setImporting(false);
        setAbout(false);
      }
      if (e.key === "Tab") {
        const items = Array.from(
          document.querySelectorAll<HTMLElement>(
            ".modal button:not(:disabled),.modal input:not(.file-input),.modal select",
          ),
        );
        const first = items[0],
          last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.querySelector<HTMLElement>(".modal button")?.focus();
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("keydown", close);
      previous?.focus();
    };
  }, [importing, about, busy]);
  const navigate = (p: Page) => {
    location.hash = p.replaceAll(" ", "-").toLowerCase();
    setPage(p);
  };
  const run = useCallback(
    async (label: string, operation: () => Promise<void>) => {
      setBusy(label);
      setError("");
      setMessage("");
      try {
        await operation();
        setOnline(true);
      } catch (e) {
        if (
          e instanceof TypeError ||
          (e instanceof Error &&
            e.message.startsWith("Cannot reach the local API"))
        )
          setOnline(false);
        setError(
          e instanceof Error ? e.message : "The local operation failed.",
        );
      } finally {
        setBusy("");
      }
    },
    [],
  );
  const selected = workspace?.nodes.find((n) => n.id === selectedId);
  const focusEdge = (key: string | null) => {
    setFocusedRelation(key);
    setTab("dependencies");
    const edge = workspace?.edges.find((e) => relationKey(e) === key);
    if (!edge) return;
    if (
      kind !== "all" &&
      (edge.source.kind !== kind || edge.target.kind !== kind)
    ) {
      setKind("all");
      setMessage(
        "Showing all node kinds so the focused relationship is visible.",
      );
    }
    if (selectedId !== edge.source_id && selectedId !== edge.target_id)
      setSelectedId(edge.source_id);
  };

  const visible = useMemo(
    () =>
      workspace?.nodes.filter(
        (n) => kind === "all" || n.identity.kind === kind,
      ) ?? [],
    [workspace, kind],
  );
  const listed = visible.filter(
    (n) =>
      (classification === "all" || n.impact_class === classification) &&
      `${n.name} ${identityLabel(n.identity)}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const paths = workspace
    ? selected
      ? selectedPaths(workspace, selected)
      : (workspace.result?.impact.paths ?? [])
    : [];
  const relations = workspace
    ? selected
      ? neighborRelations(workspace, selected)
      : workspace.edges
    : [];
  const missing = workspace?.edges.filter((e) => !e.provenance.length) ?? [];
  const loadDemo = () =>
    run("Loading demo", async () => {
      const next = await request<Workspace>("demo", {});
      setWorkspace(next);
      setFocusedRelation(null);
      setSelectedId(
        next.nodes.find((n) => n.impact_class === "Changed")?.id ?? "",
      );
      setKind("table");
      setSearch("");
      setClassification("all");
      setMessage("Synthetic demo loaded and analyzed by the Python engine.");
    });
  const chooseScenario = (id: string) => {
    void run("Loading recorded scenario", async () => {
      const next = await runtime.loadScenario(id);
      setWorkspace(next);
      setActiveScenario(id);
      setSelectedId(next.nodes.find(node => node.impact_class === "Changed")?.id ?? "");
      setFocusedRelation(null);
      setKind("table");
      setSearch("");
      setClassification("all");
      setTab("dependencies");
      navigate("Impact Analysis");
      setMessage("Recorded scenario loaded. Results and report were generated in advance by Python; no live analysis is performed.");
    });
  };
  const analyze = () => {
    if (!workspace || !selected || !workspace.manifest) return;
    void run("Analyzing impact", async () => {
      const next = await request<Workspace>("impact", {
        manifest: workspace.manifest,
        namespace: workspace.namespace,
        changes: {
          changes_schema: "governance-impact-changes",
          changes_version: "1",
          changed_nodes: [selected.identity],
        },
      });
      setFocusedRelation(null);
      setWorkspace({
        ...next,
        manifest: workspace.manifest,
        namespace: workspace.namespace,
        synthetic: workspace.synthetic,
      });
      setMessage(
        `Analysis completed: ${next.result?.status} in the supplied graph. No remote writes.`,
      );
    });
  };
  const exportReport = () => {
    if (!workspace?.result) return;
    const resultToExport = workspace.result;
    void run("Exporting report", async () => {
      const url = URL.createObjectURL(await runtime.report(resultToExport, activeScenario));
      const link = document.createElement("a");
      link.href = url;
      link.download = publicMode ? `${activeScenario}-report.html` : "governance-impact-report.html";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(
        publicMode ? "Pre-generated Python report verified and downloaded for this scenario." : "HTML report generated by the Python renderer and downloaded.",
      );
    });
  };
  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    await run("Validating artifact", async () => {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Maximum file size is 2 MiB.");
      let value: unknown;
      try {
        value = JSON.parse(await file.text());
      } catch {
        throw new Error("The selected file is not valid JSON.");
      }
      if (importKind === "plan") {
        setPlan(await request<Plan>("plan", value));
        navigate("Plans");
      } else if (importKind === "review") {
        setReview(await request<Review>("review", value));
        navigate("Policies");
      } else {
        const next = await request<Workspace>(
          importKind,
          importKind === "graph" ? { manifest: value, namespace } : value,
        );
        setWorkspace(next);
        setFocusedRelation(null);
        setSelectedId(
          next.nodes.find((n) => n.impact_class === "Changed")?.id ??
            next.nodes.find((n) => n.identity.kind === "table")?.id ??
            next.nodes[0]?.id ??
            "",
        );
        setKind(
          next.nodes.some((n) => n.identity.kind === "table") ? "table" : "all",
        );
        setSearch("");
        setClassification("all");
        navigate("Impact Analysis");
      }
      setImporting(false);
      setMessage(`${file.name} validated and loaded locally.`);
    });
  };
  const openImport = (type: ImportKind) => {
    setImportKind(type);
    setImporting(true);
  };
  const assessment = review ? reviewAssessment(review) : null;
  const result = workspace?.result;
  const metrics = [
    {
      label: "Graph nodes",
      value: workspace?.nodes.length ?? "—",
      hint: workspace
        ? workspace.coverage === "Full supplied graph"
          ? `${workspace.nodes.filter((n) => n.identity.kind === "table").length} tables · ${workspace.nodes.filter((n) => n.identity.kind !== "table").length} other identities`
          : "Identities in result evidence"
        : "Load a graph to begin",
      icon: <Database />,
    },
    {
      label: "Changes analyzed",
      value: result?.impact.changed_nodes.length ?? "—",
      hint: "Explicit hypothetical changes",
      icon: <FileCheck2 />,
    },
    {
      label: "Direct impacts",
      value: result?.impact.direct_nodes.length ?? "—",
      hint: "Immediate impact hops",
      icon: <Zap />,
    },
    {
      label: "Transitive impacts",
      value: result?.impact.transitive_nodes.length ?? "—",
      hint: "From recorded engine paths",
      icon: <GitBranch />,
    },
    {
      label: "Policy matches",
      value: result?.policy_identity ? result.affected_policies.length : "—",
      hint: result?.policy_identity
        ? "Matches, not violations"
        : "No policy set supplied",
      icon: <Shield />,
    },
  ];
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#dashboard">
          Adrián Martínez Martín<span> / </span>
          <b>{publicMode ? "Explorer" : "Governance"}</b>
        </a>
        <nav aria-label="Main navigation">
          {(publicMode && !workspace ? [] : pages).map((p) => (
            <a
              key={p}
              className={page === p ? "active" : ""}
              href={`#${p.replaceAll(" ", "-").toLowerCase()}`}
            >
              {p}
            </a>
          ))}
        </nav>
        <div className="top-tools">
          {!publicMode && <button
            className="icon-button"
            aria-label="Workspace information"
            onClick={() => setAbout(true)}
          >
            <Settings2 size={18} />
          </button>}
          <span className={`connection ${online ? "online" : ""}`}>
            <i />
            {publicMode ? "Static demo" : online ? "Local" : "API offline"}
          </span>
        </div>
      </header>
      <main className={publicMode ? "public-explorer" : undefined}>
        {publicMode && !workspace ? <PublicWelcome scenarios={runtime.scenarios} busy={!!busy} error={error} onChoose={chooseScenario} /> : <>
        <section className="hero">
          <div>
            <div className="eyebrow">
              Data governance <span className="slash">/</span> {publicMode ? "Public demo" : "Local workbench"}
            </div>
            <h1>{publicMode ? "Governance Explorer" : "Governance Control Center"}</h1>
            <p>Explore dependencies. Understand impact. Follow the evidence.</p>
            <div className="hero-meta">
              <span>
                <Shield size={13} />
                Read-only by design
              </span>
              <span>{publicMode ? "Synthetic evidence · precomputed by Python · no live analysis" : "Session-only workspace · reload clears files; export first"}</span>
              {workspace?.synthetic && (
                <Badge tone="rose">Synthetic demo</Badge>
              )}
            </div>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <Database />
            <GitBranch />
          </div>
        </section>
        <div className="workspace-toolbar">
          <div>
            <span className="section-label">{page}</span>
            <span className="toolbar-divider" />
            {workspace ? (
              <>
                <span className="muted">{workspace.coverage}</span>
                {result && (
                  <Badge tone={result.impact_detected ? "rose" : "neutral"}>
                    {result.status === "impacted"
                      ? "Impact identified"
                      : "No impact identified"}
                  </Badge>
                )}
              </>
            ) : (
              <span className="muted">
                No graph loaded · start with the offline demo
              </span>
            )}
          </div>
          <div className="button-row">
            {publicMode ? <label className="scenario-select">Explore scenario
              <select aria-label="Explore scenario" disabled={!!busy} value={activeScenario ?? ""} onChange={event => chooseScenario(event.target.value)}>
                {runtime.scenarios.map(scenario => <option key={scenario.id} value={scenario.id}>{scenario.title}</option>)}
              </select>
            </label> : <>
            <button disabled={!!busy} onClick={() => openImport("graph")}><Upload size={15} />Import artifact</button>
            <button className="primary" disabled={!!busy} onClick={() => void loadDemo()}>
              {busy === "Loading demo" ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}Load demo
            </button></>}
          </div>
        </div>
        {publicMode && <div className="scenario-context panel"><strong>{runtime.scenarios.find(item => item.id === activeScenario)?.title}</strong><p>{runtime.scenarios.find(item => item.id === activeScenario)?.description}</p><span>Explore this recorded result. Arbitrary imports and reanalysis require the local Full Workbench.</span></div>}

        {(busy || error || message) && (
          <div
            className={`notice ${error ? "error" : ""}`}
            role={error ? "alert" : "status"}
          >
            {busy ? (
              <>
                <LoaderCircle className="spin" size={15} />
                {busy}…
              </>
            ) : error ? (
              <>
                <TriangleAlert size={16} />
                {error}
              </>
            ) : (
              <>
                <Check size={16} />
                {message}
              </>
            )}
            {!busy && (
              <button
                className="icon-button"
                aria-label="Dismiss notification"
                onClick={() => {
                  setMessage("");
                  setError("");
                }}
              >
                <X size={15} />
              </button>
            )}
          </div>
        )}
        {(page === "Impact Analysis" || page === "Lineage") && (
          <div className="workbench-layout">
            <aside className="panel sidebar">
              <div className="eyebrow">Loaded graph nodes</div>
              <label className="search">
                <Search size={16} />
                <input
                  aria-label="Search loaded assets"
                  placeholder="Search loaded assets…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <label className="field-label">
                Asset type
                <select
                  value={kind}
                  onChange={(e) => {
                    const value = e.target.value;
                    setFocusedRelation(null);
                    setKind(value);
                    setSelectedId(selectionForKind(workspace?.nodes ?? [], selectedId, value));
                  }}
                >
                  <option value="all">All kinds</option>
                  {[
                    ...new Set(
                      workspace?.nodes.map((n) => n.identity.kind) ?? ["table"],
                    ),
                  ].map((k) => (
                    <option key={k} value={k}>
                      {k.replace("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                Impact classification
                <select
                  value={classification}
                  onChange={(e) => setClassification(e.target.value)}
                >
                  <option value="all">All classifications</option>
                  {[
                    "Changed",
                    "Direct",
                    "Transitive",
                    "Not reached",
                    "Not analyzed",
                  ].map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <div className="sidebar-list-heading">
                {kind === "table"
                  ? "Tables"
                  : kind === "all"
                    ? "Graph nodes"
                    : kind.replace("_", " ")}{" "}
                <span>
                  {listed.length}
                  {listed.length !== visible.length
                    ? ` / ${visible.length}`
                    : ""}
                </span>
              </div>
              <div className="asset-list">
                {listed.map((n) => (
                  <button
                    key={n.id}
                    disabled={!!busy}
                    className={selectedId === n.id ? "selected" : ""}
                    onClick={() => selectNode(n.id)}
                  >
                    <Table2 size={17} />
                    <span>
                      <strong>{n.name}</strong>
                      <small title={identityLabel(n.identity)}>
                        {n.identity.namespace} ·{" "}
                        {n.identity.kind.replace("_", " ")}
                      </small>
                    </span>
                    <Badge
                      tone={n.impact_class === "Changed" ? "rose" : "neutral"}
                    >
                      {n.impact_class}
                    </Badge>
                  </button>
                ))}
                {!listed.length && (
                  <p className="muted sidebar-empty">
                    {workspace
                      ? "No assets match these filters."
                      : "Load a graph or the synthetic demo to inspect assets."}
                  </p>
                )}
              </div>
              <div className="sidebar-footer">
                <Info size={14} />
                Only loaded graph evidence. No remote catalog discovery.
              </div>
            </aside>
            <section className="center-column">
              <div className="metrics compact">
                {metrics
                  .filter((_, i) => i !== 1)
                  .map((m) => (
                    <div className="metric" key={m.label}>
                      <div>
                        {m.icon}
                        <span>{m.label}</span>
                      </div>
                      <strong>{m.value}</strong>
                      <small>{m.hint}</small>
                    </div>
                  ))}
              </div>
              <section className="panel graph-panel">
                <div className="panel-heading">
                  <h2>Lineage & impact</h2>
                  <div className="legend">
                    <span>
                      <i className="rose-dot" />
                      Changed
                    </span>
                    <span title="Direct: one recorded dependency hop from the hypothetical change">
                      <i className="direct-dot" />
                      Direct
                    </span>
                    <span title="Transitive: reached through one or more intermediate consumers">
                      <i />
                      Transitive
                    </span>
                  </div>
                </div>
                {workspace && visible.length ? (
                  <ReactFlowProvider>
                    <Graph
                      workspace={workspace}
                      selected={selected}
                      visible={visible}
                      onSelect={selectNode}
                      focused={focusedRelation}
                      onFocus={focusEdge}
                    />
                  </ReactFlowProvider>
                ) : (
                  <div className="graph-empty">
                    <Empty
                      icon={<GitBranch />}
                      title={
                        workspace
                          ? "No nodes of this kind"
                          : "Your evidence starts here"
                      }
                    >
                      Load the synthetic demo or import a local dbt manifest to
                      explore real relationships.
                    </Empty>
                    {!workspace && (
                      <button
                        className="primary"
                        disabled={!!busy}
                        onClick={() => void loadDemo()}
                      >
                        <Play size={15} />
                        Load offline demo
                      </button>
                    )}
                  </div>
                )}
                <div className="graph-caption">
                  <ArrowRight size={14} />
                  Dependency → consumer for depends_on; other edges retain
                  stored direction.
                  <span>Dashed nodes: no recorded dependency edges.</span>
                </div>
              </section>
              <section className="evidence-panel">
                <div className="tabs" role="tablist">
                  {[
                    {
                      id: "dependencies",
                      label: `Recorded relationships (${relations.length})`,
                      icon: <GitBranch size={16} />,
                    },
                    {
                      id: "paths",
                      label: `Impact paths (${paths.length})`,
                      icon: <Layers3 size={16} />,
                    },
                    {
                      id: "coverage",
                      label: "Coverage & evidence",
                      icon: <TriangleAlert size={16} />,
                    },
                  ].map((t) => (
                    <button
                      role="tab"
                      aria-selected={tab === t.id}
                      key={t.id}
                      className={tab === t.id ? "active" : ""}
                      onClick={() => setTab(t.id)}
                    >
                      {t.icon}
                      {t.label}
                    </button>
                  ))}
                </div>
                <div className="panel evidence-body" role="tabpanel">
                  {tab === "dependencies" ? (
                    <>
                      <p className="muted">
                        Stored relationships{" "}
                        {selected ? (
                          <>
                            for <strong>{selected.name}</strong>
                          </>
                        ) : (
                          "in the loaded graph"
                        )}
                        . Arrows below follow the artifact, not the
                        visualization.
                      </p>
                      <Relations
                        edges={relations}
                        selected={selected}
                        focused={focusedRelation}
                        onFocus={focusEdge}
                        onSelect={selectNode}
                      />
                    </>
                  ) : tab === "paths" ? (
                    <PathCards paths={paths} />
                  ) : (
                    <>
                      <h3>Evidence has boundaries</h3>
                      <p>
                        {workspace?.coverage ?? "No graph or result loaded."}
                      </p>
                      <p>
                        Impact and clear statuses apply only to supplied
                        evidence. Neither is a completeness or production-safety
                        guarantee.
                      </p>
                      <p>
                        {missing.length} loaded relationships (including
                        hierarchy edges) have no provenance recorded.
                      </p>
                      <p>
                        Column lineage, undeclared dependencies, runtime
                        breakage and business severity remain unknown unless
                        explicitly supplied.
                      </p>
                      {result && (
                        <JsonDetails
                          title="Artifact identities"
                          value={{
                            content: result.content_identity,
                            graph: result.graph_identity,
                          }}
                        />
                      )}
                    </>
                  )}
                </div>
              </section>
            </section>
            {workspace ? (
              <Inspector
                node={selected}
                workspace={workspace}
                busy={!!busy}
                onAnalyze={analyze}
                onExport={exportReport}
                onPolicies={() => navigate("Policies")}
                onSelect={selectNode}
                publicDemo={publicMode}
              />
            ) : (
              <aside className="panel inspector">
                <Empty icon={<Table2 />} title="An asset, in context">
                  Select a node after loading evidence to see relationships,
                  provenance and analysis actions.
                </Empty>
              </aside>
            )}
          </div>
        )}
        {page === "Dashboard" && (
          <>
            <div className="metrics dashboard-metrics">
              {metrics.map((m) => (
                <div className="metric" key={m.label}>
                  <div>
                    {m.icon}
                    <span>{m.label}</span>
                  </div>
                  <strong>{m.value}</strong>
                  <small>{m.hint}</small>
                </div>
              ))}
            </div>
            <div className="dashboard-grid">
              <section className="panel padded">
                <div className="panel-heading">
                  <h2>Analysis overview</h2>
                  <Activity size={18} />
                </div>
                {result ? (
                  <>
                    <Badge tone={result.impact_detected ? "rose" : "green"}>
                      {result.status === "impacted"
                        ? "Impact identified"
                        : "No impact identified in supplied graph"}
                    </Badge>
                    <h2 className="overview-title">
                      Every impact has a recorded reason.
                    </h2>
                    <p>
                      {result.impact.changed_nodes
                        .map((n) => n.logical_id)
                        .join(", ")}{" "}
                      → {result.impact.direct_nodes.length} direct,{" "}
                      {result.impact.transitive_nodes.length} transitive
                      impacts.
                    </p>
                    <PathCards paths={result.impact.paths} />
                    <button onClick={() => navigate("Impact Analysis")}>
                      <GitBranch size={16} />
                      Explore analysis
                      <ChevronRight size={15} />
                    </button>
                  </>
                ) : (
                  <Empty title="No analysis yet">
                    Load the synthetic demo or select a node in an imported dbt
                    graph to run the Python impact engine.
                  </Empty>
                )}
              </section>
              <section className="panel padded">
                <div className="eyebrow">Workspace boundaries</div>
                <h2>Local. Explicit. Evidence-led.</h2>
                <ul className="boundary-list">
                  <li>
                    <Check />
                    No tenant connections or remote mutations
                  </li>
                  <li>
                    <Check />
                    Engine-owned impact and v1 artifacts
                  </li>
                  <li>
                    <Check />
                    Reports rendered by Python, not the browser
                  </li>
                  <li>
                    <Info />
                    No complete catalog or automatic change detection
                  </li>
                  <li>
                    <Info />
                    No business-risk or compliance score
                  </li>
                </ul>
                <p className="muted">
                  {workspace?.synthetic
                    ? "The loaded demo is synthetic. It is not production validation."
                    : "Files remain in this session and are not persisted by the server."}
                </p>
                <button disabled={!result || !!busy} onClick={exportReport}>
                  <ArrowDownToLine size={16} />
                  {publicMode ? "Download scenario report" : "Export current analysis"}
                </button>
              </section>
            </div>
          </>
        )}
        {page === "Policies" && (
          <div className="policy-grid">
            <section className="panel padded">
              <div className="panel-heading">
                <h2>Policy matches</h2>
                <Shield size={18} />
              </div>
              <p className="muted">
                Matches show relevance to the impact set. They do not
                demonstrate a violation.
              </p>
              {result?.affected_policies.length ? (
                <div className="policy-list">
                  {result.affected_policies.map((p) => (
                    <article key={p.policy_id}>
                      <div>
                        <Shield size={17} />
                        <h3>{p.policy_id}</h3>
                        <Badge>{p.severity}</Badge>
                        <Badge tone="rose">Match only</Badge>
                      </div>
                      <p>
                        {p.rule_type.replaceAll("_", " ")} ·{" "}
                        {p.matched_objects.length} matching objects
                      </p>
                      <ul>
                        {p.matched_objects.map((o, i) => (
                          <li key={i}>
                            {identityLabel(o.node)}{" "}
                            <small>({o.object_kind})</small>
                          </li>
                        ))}
                      </ul>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty
                  icon={<Shield />}
                  title={
                    result?.policy_identity
                      ? "No matching policies recorded"
                      : "No policy set supplied"
                  }
                >
                  {result?.policy_identity
                    ? "This result contains no policy matches. It is not proof of compliance."
                    : "The offline demo supplies no policies. Import an impact result with policy evidence to inspect actual matches."}
                </Empty>
              )}
              <button disabled={!!busy} onClick={() => openImport("result")}>
                <Upload size={15} />
                Import impact result
              </button>
            </section>
            <section className="panel padded">
              <div className="panel-heading">
                <h2>Conflicts & review evidence</h2>
                <button disabled={!!busy} onClick={() => openImport("review")}>
                  <Upload size={15} />
                  Import review
                </button>
              </div>
              {review ? (
                <>
                  <h3>{assessment?.title}</h3>
                  <p className="safety-strip">{assessment?.detail}</p>
                  <p className="muted">
                    Separate governance-ci-review-result v1 artifact. It is not
                    automatically associated with the current impact graph.
                  </p>
                  {assessment?.assessed && <div className="summary-grid">
                    {Object.entries(review.conflicts.summary).map(([k, v]) => (
                      <div key={k}>
                        <small>{k.replaceAll("_", " ")}</small>
                        <strong>{v}</strong>
                      </div>
                    ))}
                  </div>}
                  {assessment?.assessed && (review.conflicts.conflict_report?.results.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Object / property</th>
                            <th>Recorded finding</th>
                            <th>Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {review.conflicts.conflict_report.results.map(
                            (r, i) => (
                              <tr key={i}>
                                <td>
                                  {identityLabel(r.object)}
                                  <small>{r.property}</small>
                                </td>
                                <td>
                                  <Badge
                                    tone={
                                      r.state.includes("CONFLICT")
                                        ? "amber"
                                        : "neutral"
                                    }
                                  >
                                    {r.state.replaceAll("_", " ")}
                                  </Badge>
                                </td>
                                <td>
                                  {r.reason}
                                  <JsonDetails
                                    title="Recorded observation groups"
                                    value={r.value_groups}
                                  />
                                </td>
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>No conflict findings recorded by the supplied assessment. Coverage remains limited to that evidence.</p>
                  ))}
                  <JsonDetails
                    title="Reconciliation assessments (recorded, not executed)"
                    value={review.conflicts.reconciliation_assessments}
                  />
                  <JsonDetails
                    title="Review identity & drift context"
                    value={{
                      identity: review.content_identity,
                      drift: review.drift,
                    }}
                  />
                </>
              ) : (
                <Empty
                  icon={<FileCheck2 />}
                  title="No conflict evidence loaded"
                >
                  Impact v1 does not carry a conflict report. Import a
                  compatible review artifact; absent evidence is not a clear
                  assessment.
                </Empty>
              )}
            </section>
          </div>
        )}
        {page === "Plans" && (
          <section className="panel padded plan-panel">
            <div className="panel-heading">
              <div>
                <div className="eyebrow">Saved governance plans</div>
                <h2>Inspect before any decision</h2>
              </div>
              <button disabled={!!busy} onClick={() => openImport("plan")}>
                <FolderOpen size={16} />
                Open saved plan
              </button>
            </div>
            <div className="safety-strip">
              <Shield size={17} />
              Inspection only. There is no apply endpoint, tenant connection or
              execution control.
            </div>
            {plan ? (
              <>
                <div className="plan-meta">
                  <div>
                    <span>Target provider</span>
                    <strong>{plan.target_context.provider}</strong>
                  </div>
                  <div>
                    <span>Recorded mode</span>
                    <strong>{plan.target_context.mode}</strong>
                  </div>
                  <div>
                    <span>Artifact version</span>
                    <strong>{plan.plan_version}</strong>
                  </div>
                  <div>
                    <span>Planned entries</span>
                    <strong>{plan.actions.length}</strong>
                  </div>
                </div>
                <p className="identity">
                  Content identity: {plan.content_identity.digest}
                </p>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Planned operation</th>
                        <th>Object / destination</th>
                        <th>Recorded reason</th>
                        <th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plan.actions.map((a, i) => (
                        <tr key={i}>
                          <td>
                            <Badge
                              tone={
                                a.action_type === "create" ||
                                a.action_type === "update"
                                  ? "rose"
                                  : "neutral"
                              }
                            >
                              {a.action_type}
                            </Badge>
                            <small>{a.object_kind}</small>
                          </td>
                          <td>{a.local_id ?? a.remote_id ?? "Not recorded"}</td>
                          <td>{a.reason || "Not recorded"}</td>
                          <td>
                            <JsonDetails
                              title="Desired object & changed fields"
                              value={{
                                asset: a.desired_asset,
                                relationship: a.desired_relationship,
                                changed_fields: a.changed_fields,
                              }}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!plan.actions.length && <p>No planned actions recorded.</p>}
                <h3>Warnings & reconciliation evidence</h3>
                <p className="muted">
                  Remote freshness is not checked. Artifact integrity does not
                  authorize execution or prove that a plan is currently safe.
                </p>
                {plan.reconciliation_assumptions?.actions.length ? (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Planned asset</th>
                          <th>Property</th>
                          <th>Recorded decision</th>
                          <th>Reason / role</th>
                        </tr>
                      </thead>
                      <tbody>
                        {plan.reconciliation_assumptions.actions.flatMap(
                          (a, i) =>
                            a.properties.map((p, j) => (
                              <tr key={`${i}-${j}`}>
                                <td>{a.local_id}</td>
                                <td>{p.property}</td>
                                <td>
                                  <Badge>
                                    {p.decision?.state.replaceAll("_", " ") ??
                                      "Not recorded"}
                                  </Badge>
                                </td>
                                <td>
                                  {p.decision?.reason ??
                                    "Insufficient evidence"}
                                  <small>{p.roles.join(", ")}</small>
                                </td>
                              </tr>
                            )),
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted">
                    No property-level reconciliation decisions recorded in this
                    plan. This is not an execution approval.
                  </p>
                )}
                <JsonDetails
                  title="Recorded reconciliation assumptions and blockers"
                  value={
                    plan.reconciliation_assumptions ?? "Not included in plan v1"
                  }
                />
              </>
            ) : (
              <Empty icon={<FileJson />} title="No saved plan loaded">
                Open a governance-plan v1 or v2 .gplan file. Its existing loader
                checks schema, integrity and supported actions. Plans are never
                generated or applied by this workspace.
              </Empty>
            )}
          </section>
        )}
        <footer className="page-footer">
          <span>{publicMode ? "Governance Explorer" : "Governance Control Center"}</span>
          <span>{publicMode ? "Precomputed synthetic evidence · no backend · no remote writes" : "Local evidence · deterministic engine · no remote writes"}</span>
        </footer>
        </>}
      </main>
      {!publicMode && (importing || about) && (
        <div
          className="modal-backdrop"
          onClick={() => {
            if (!busy) {
              setImporting(false);
              setAbout(false);
            }
          }}
        >
          <section
            className="modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="close icon-button"
              aria-label="Close dialog"
              disabled={!!busy}
              onClick={() => {
                setImporting(false);
                setAbout(false);
              }}
            >
              <X />
            </button>
            {about ? (
              <>
                <div className="eyebrow">Local workspace</div>
                <h2 id="dialog-title">Evidence, without remote writes.</h2>
                <p>
                  This optional workbench calls the existing Python governance
                  APIs. Inputs stay local and are held only for this session.
                </p>
                <ul>
                  <li>
                    Loopback-only API. No CORS wildcard or credential discovery.
                  </li>
                  <li>Input limit: 2 MiB, 200 nodes, 1,000 relationships.</li>
                  <li>
                    No automatic catalog discovery, mutations or plan execution.
                  </li>
                </ul>
                <p className="muted">
                  Reloading clears imported artifacts. Export a report before
                  ending your session.
                </p>
              </>
            ) : (
              <>
                <div className="eyebrow">Local file import</div>
                <h2 id="dialog-title">Bring your own evidence</h2>
                <p className="muted">
                  Validated by the Python loaders. No filesystem path or
                  external URL is accepted.
                </p>
                <label className="field-label">
                  Artifact type
                  <select
                    value={importKind}
                    onChange={(e) =>
                      setImportKind(e.target.value as ImportKind)
                    }
                  >
                    <option value="graph">dbt manifest v12 · full graph</option>
                    <option value="result">
                      Impact result v1 · evidence only
                    </option>
                    <option value="review">
                      CI review result v1 · conflicts
                    </option>
                    <option value="plan">Saved governance plan v1 / v2</option>
                  </select>
                </label>
                {importKind === "graph" && (
                  <label className="field-label">
                    Logical namespace
                    <input
                      value={namespace}
                      maxLength={200}
                      onChange={(e) => setNamespace(e.target.value)}
                      placeholder="e.g. analytics"
                    />
                  </label>
                )}
                <input
                  ref={input}
                  className="file-input"
                  type="file"
                  accept=".json,.gplan,application/json"
                  onChange={(e) => void upload(e)}
                  aria-label="Choose local JSON artifact"
                />
                <button
                  className="primary import-button"
                  disabled={
                    !!busy || (importKind === "graph" && !namespace.trim())
                  }
                  onClick={() => input.current?.click()}
                >
                  <Upload size={16} />
                  {busy || "Choose a local file"}
                </button>
                <p className="footnote">
                  2 MiB maximum. Impact results contain only recorded evidence,
                  not the full graph. Loading one cannot recover disconnected
                  assets.
                </p>
                {error && (
                  <p className="notice error" role="alert">
                    {error}
                  </p>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
