import type { ImpactResult, Workspace } from "./model";
export type Scenario = {
  id: string;
  title: string;
  description: string;
  contract: string;
  contract_version: string;
  result_identity: { digest: string };
  files: Record<string, string>;
};
export type WorkbenchRuntime = {
  kind: "local" | "public";
  scenarios: readonly Scenario[];
  request: <T>(endpoint: string, body?: unknown) => Promise<T>;
  loadScenario: (id: string) => Promise<Workspace>;
  report: (result: ImpactResult, scenarioId: string | null) => Promise<Blob>;
};
