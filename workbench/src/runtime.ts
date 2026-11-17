import { request, reportBlob } from "./model";
import type { WorkbenchRuntime } from "./runtime.types";
export const runtime: WorkbenchRuntime = {
  kind: "local",
  scenarios: [],
  request,
  loadScenario: async () => { throw new Error("Use the local demo or import an artifact."); },
  report: reportBlob,
};
