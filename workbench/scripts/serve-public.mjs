// Read-only localhost transport for the static build. No API or analysis code.
import { createServer } from "node:http";
import { readFile, realpath, stat } from "node:fs/promises";
import { resolve, relative, extname } from "node:path";
const root = await realpath(resolve(process.argv[2] ?? "workbench/dist-public"));
const port = Number(process.argv[3] ?? 8787);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid local port");
const mime = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8" };
createServer(async (req, res) => {
  console.log(req.method, req.url);
  if (!["GET", "HEAD"].includes(req.method)) { res.writeHead(405).end(); return; }
  try {
    const pathname = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
    if (pathname.startsWith("/api/") || pathname.split("/").some(part => part.startsWith("."))) { res.writeHead(404).end(); return; }
    let file = resolve(root, "." + pathname);
    if ((await stat(file)).isDirectory()) file = resolve(file, "index.html");
    file = await realpath(file);
    const within = relative(root, file);
    if (within.startsWith("..") || within.includes(":")) { res.writeHead(404).end(); return; }
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": mime[extname(file)] ?? "application/octet-stream", "X-Content-Type-Options": "nosniff", "Content-Length": body.length });
    res.end(req.method === "HEAD" ? undefined : body);
  } catch { res.writeHead(404).end(); }
}).listen(port, "127.0.0.1", () => console.log(`Static files only: http://127.0.0.1:${port}`));
