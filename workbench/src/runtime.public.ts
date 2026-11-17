import manifest from "./generated/public-scenarios.json";
import type { ImpactResult, Workspace } from "./model";
import type { Scenario, WorkbenchRuntime } from "./runtime.types";

export const scenarios: readonly Scenario[] = manifest.scenarios;
export function scenarioById(id: string): Scenario {
  const scenario = scenarios.find(item => item.id === id);
  if (!scenario) throw new Error("Choose one of the supplied synthetic scenarios.");
  return scenario;
}

/** Only manifest-authorized same-origin static files can be read. No URL input. */
export async function verifiedAsset(id: string, name: string): Promise<ArrayBuffer> {
  const scenario = scenarioById(id);
  if (!Object.hasOwn(scenario.files, name)) throw new Error("Unknown scenario resource.");
  const response = await fetch(`${import.meta.env.BASE_URL}scenarios/${scenario.id}/${name}`, { credentials: "omit" });
  if (!response.ok) throw new Error("Scenario files are unavailable. Retry loading this static demo.");
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
  if (hex !== scenario.files[name]) throw new Error("Scenario file integrity mismatch. No result was loaded.");
  return bytes;
}
export async function loadScenario(id: string): Promise<Workspace> {
  const bytes = await verifiedAsset(id, "workspace.json");
  const workspace = JSON.parse(new TextDecoder().decode(bytes)) as Workspace;
  if (workspace.result?.content_identity.digest !== scenarioById(id).result_identity.digest)
    throw new Error("The recorded result does not match this scenario.");
  return { ...workspace, can_analyze: false };
}
export async function scenarioReport(result: ImpactResult, id: string | null): Promise<Blob> {
  if (!id || result.content_identity.digest !== scenarioById(id).result_identity.digest)
    throw new Error("Choose the scenario corresponding to this recorded analysis.");
  return new Blob([await verifiedAsset(id, "report.html")], { type: "text/html;charset=utf-8" });
}
export const runtime: WorkbenchRuntime = {
  kind: "public",
  scenarios,
  request: async () => { throw new Error("Live operations are available only in the local Full Workbench."); },
  loadScenario,
  report: scenarioReport,
};
