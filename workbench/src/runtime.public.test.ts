import { describe, it, expect, vi, afterEach } from "vitest";
import workspaceText from "../public-demo/scenarios/orders-impact/workspace.json?raw";
import reportText from "../public-demo/scenarios/orders-impact/report.html?raw";
import { runtime, loadScenario, scenarioReport, scenarioById, verifiedAsset } from "./runtime.public";
const workspace = JSON.parse(workspaceText);
const bytes = (text: string) => new TextEncoder().encode(text).buffer;
afterEach(() => vi.unstubAllGlobals());
describe("static scenario boundary", () => {
  it("never maps arbitrary IDs or filenames to URLs", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect(() => scenarioById("../../private")).toThrow("supplied synthetic");
    await expect(verifiedAsset("orders-impact", "../../.env")).rejects.toThrow("Unknown");
    await expect(runtime.request("impact", {})).rejects.toThrow("local Full Workbench");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("loads only hash-verified precomputed evidence and disables live analysis", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => bytes(workspaceText) });
    vi.stubGlobal("fetch", fetch);
    const loaded = await loadScenario("orders-impact");
    expect(loaded.result).toEqual(workspace.result);
    expect(loaded.can_analyze).toBe(false);
    expect(fetch.mock.calls[0][0]).toMatch(/scenarios\/orders-impact\/workspace.json$/);
    expect(fetch.mock.calls[0][1]).toEqual({ credentials: "omit" });
  });
  it("rejects changed bytes before displaying conclusions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => bytes(workspaceText + " ") }));
    await expect(loadScenario("orders-impact")).rejects.toThrow("integrity mismatch");
  });
  it("downloads the exact Python report and refuses mismatched scenarios", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => bytes(reportText) }));
    expect(await (await scenarioReport(workspace.result, "orders-impact")).text()).toBe(reportText);
    await expect(scenarioReport(workspace.result, "isolated-inventory")).rejects.toThrow("corresponding");
  });
  it("surfaces unavailable static files", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    await expect(loadScenario("orders-impact")).rejects.toThrow("unavailable");
  });
});
