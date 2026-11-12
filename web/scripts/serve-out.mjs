// Minimal static file server for the exported site in out/ (used by Playwright).
// Usage: node scripts/serve-out.mjs [port]
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve("out");
const port = Number(process.argv[2] ?? process.env.PORT ?? 3000);

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function resolveFile(urlPath) {
  const safe = normalize(decodeURIComponent(urlPath)).replace(/^([/\\]|\.\.)+/, "");
  const base = join(root, safe);
  const candidates = [base, `${base}.html`, join(base, "index.html")];
  return candidates.find((p) => p.startsWith(root) && existsSync(p) && statSync(p).isFile());
}

createServer((req, res) => {
  const file = resolveFile(new URL(req.url ?? "/", "http://localhost").pathname);
  if (!file) {
    const notFound = join(root, "404.html");
    res.writeHead(404, { "Content-Type": types[".html"] });
    if (existsSync(notFound)) createReadStream(notFound).pipe(res);
    else res.end("Not found");
    return;
  }
  res.writeHead(200, { "Content-Type": types[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
}).listen(port, () => console.log(`Serving out/ on http://localhost:${port}`));
