// Thin wrapper over the GENERATED client types (src/lib/api/schema.d.ts is produced from FastAPI's
// OpenAPI schema by `npm run gen:api`; do not hand-edit it).
import createClient from "openapi-fetch";
import type { components, paths } from "./api/schema";

export type S = components["schemas"];

function newTraceId() {
  return "tr_" + Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export const lastTrace = { id: "" };

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use({
  onRequest({ request }) {
    // one trace id per UI request -> API -> graph -> MCP servers -> audit log
    lastTrace.id = newTraceId();
    request.headers.set("X-Trace-Id", lastTrace.id);
    const tok = localStorage.getItem("gigbid_token");
    if (tok) request.headers.set("Authorization", `Bearer ${tok}`);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && !location.pathname.startsWith("/login")) location.assign("/login");
    return response;
  },
});

export function errText(e: unknown): string {
  if (!e) return "";
  if (typeof e === "string") return e;
  const d = (e as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).join("; ");
  return JSON.stringify(e);
}

export function money(v?: number | null, type?: string | null) {
  if (v == null) return "–";
  return `$${Math.round(v).toLocaleString()}${type === "hourly" ? "/h" : ""}`;
}

export function pct(v?: number | null) {
  return v == null ? "–" : `${Math.round(v * 100)}%`;
}

export function ago(ts?: number | string | null) {
  if (ts == null) return "";
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
