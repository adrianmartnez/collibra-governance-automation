import { ArrowRight, GitBranch, Shield, FileCheck2 } from "lucide-react";
import type { Scenario } from "./runtime.types";
export function PublicWelcome({ scenarios, busy, error, onChoose }: {
  scenarios: readonly Scenario[]; busy: boolean; error: string; onChoose: (id: string) => void;
}) {
  return <section className="public-welcome">
    <div className="welcome-intro">
      <div className="eyebrow">Governance Explorer / Public demo</div>
      <h1>Before you change a table,<br />follow its dependencies.</h1>
      <p className="welcome-lead">See which recorded consumers could be affected, follow the evidence, and take an impact report with you.</p>
      {error && <p className="notice error" role="alert">{error}</p>}
      <div className="button-row"><button className="primary" disabled={busy} onClick={() => onChoose(scenarios[0].id)}>{busy ? "Loading recorded scenario…" : "Try interactive demo"} <ArrowRight size={17} /></button><span className="muted">No account, installation or connection to your data.</span></div>
      <p className="demo-disclosure"><Shield size={15} /> Synthetic scenarios · results precomputed by the Python engine · no live analysis</p>
    </div>
    <div className="scenario-grid">{scenarios.map((scenario, i) => <article className="panel scenario-card" key={scenario.id}>
      <span className="eyebrow">Scenario 0{i + 1}</span><GitBranch className="scenario-icon" /><h2>{scenario.title}</h2><p>{scenario.description}</p>
      <button disabled={busy} onClick={() => onChoose(scenario.id)}>Explore {scenario.title} <ArrowRight size={15} /></button>
    </article>)}</div>
    <div className="welcome-explainer panel">
      <div><FileCheck2 /><h2>What does impact mean here?</h2><p>A hypothetical change is a starting point in a supplied dependency graph. Direct consumers are one hop away; transitive consumers are reached through other consumers. A recorded path explains each identified impact.</p></div>
      <div><h2>A demo with explicit boundaries</h2><p>Explore graph, paths, provenance and missing evidence. Download the exact report generated in advance by Python. No imports, tenant connections or remote writes.</p><p><strong>Clear does not mean zero risk.</strong> It means no consumers were identified in the supplied graph.</p></div>
    </div>
    <section className="full-workbench-note"><h2>Need to analyze your own evidence?</h2><p>Full Workbench — Governance Control Center is the open-source local application for importing artifacts, running the Python engine, inspecting policies and plans, and exporting reports.</p><p className="muted">The GitHub download link for this integration will be enabled after it is integrated and published. This static demo does not claim an available release of the workbench.</p></section>
  </section>;
}
