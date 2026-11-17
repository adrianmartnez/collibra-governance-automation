import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
  const publicDemo = mode === "public-demo";
  return {
    base: publicDemo ? "./" : "/",
    publicDir: publicDemo ? "public-demo" : false,
    resolve: { alias: publicDemo ? [{ find: /^\.\/runtime$/, replacement: decodeURIComponent(new URL("./src/runtime.public.ts", import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1") }] : [] },
    plugins: [react(), {
      name: "public-demo-document",
      transformIndexHtml(html) {
        return publicDemo ? html.replace("<title>Governance Control Center</title>", `<title>Governance Explorer — Interactive public demo</title><meta name="description" content="Explore synthetic lineage and impact evidence, without a backend or installation."/><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"/>`) : html;
      },
    }],
    build: { outDir: publicDemo ? "dist-public" : "../src/governance/workbench/resources/frontend", emptyOutDir: true },
    server: { proxy: { "/api": { target: "http://127.0.0.1:8765" } } },
  };
});
