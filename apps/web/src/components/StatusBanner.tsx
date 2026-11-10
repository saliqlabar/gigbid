import { useEffect, useState } from "react";
import { api } from "../lib/http";

/** Degraded-mode banner: polls /api/status and shows when the marketplace MCP server or GigSim is down. */
export function StatusBanner() {
  const [st, setSt] = useState<{ ok: boolean; detail: string; queued: number } | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const r = await api.GET("/api/status");
      if (!alive) return;
      if (r.data) setSt({ ok: r.data.marketplace_available, detail: r.data.detail ?? "", queued: r.data.queued_degraded ?? 0 });
      else if (r.response?.status !== 401) setSt({ ok: false, detail: "API unreachable", queued: 0 });
    };
    tick();
    const id = setInterval(tick, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  if (!st || st.ok) return null;
  return (
    <div className="banner" role="alert" data-testid="degraded-banner">
      <strong>Marketplace unavailable.</strong> GigBid is in degraded mode: new analyses and submissions are queued
      {st.queued ? ` (${st.queued} waiting)` : ""} and resume automatically when it is back.
    </div>
  );
}
