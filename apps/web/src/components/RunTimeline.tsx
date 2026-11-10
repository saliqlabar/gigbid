import { useEffect, useRef, useState } from "react";

export type RunEvent = { ts: number; node: string; kind: string; data: Record<string, any>; trace_id?: string | null };

/** Live node-by-node progress over Server-Sent Events from /api/runs/{id}/stream. */
export function RunTimeline({ threadId, initial }: { threadId: string; initial: RunEvent[] }) {
  const [events, setEvents] = useState<RunEvent[]>(initial);
  const [live, setLive] = useState(false);
  const last = useRef(initial.at(-1)?.ts ?? 0);
  useEffect(() => {
    const es = new EventSource(`/api/runs/${encodeURIComponent(threadId)}/stream?after=${last.current}`, { withCredentials: true });
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("node", (m) => {
      const e = JSON.parse((m as MessageEvent).data) as RunEvent;
      last.current = e.ts;
      setEvents((xs) => [...xs, e]);
    });
    return () => es.close();
  }, [threadId]);
  const running = events.length > 0 && !["status"].includes(events.at(-1)!.kind) && events.at(-1)!.kind !== "interrupt";
  return (
    <div>
      <p className="small" aria-live="polite">
        {live ? <><span className="badge ok">live</span> streaming</> : <span className="badge muted">not connected</span>}
        {running && <> · <span className="spinner" aria-hidden /> working on <strong>{events.at(-1)!.node}</strong></>}
      </p>
      <ol className="timeline" aria-label="Run events">
        {events.map((e, i) => (
          <li key={i} className={e.kind}>
            <strong>{e.node}</strong> <span className="badge muted">{e.kind}</span>{" "}
            <span className="small muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
            {e.kind === "end" && <span className="small"> · {e.data.latency_s}s · {e.data.tokens} tokens{e.data.status ? ` · ${e.data.status}` : ""}</span>}
            {e.kind === "tool" && <div className="small"><code>{e.data.tool}</code> {e.data.ok ? "ok" : <span style={{ color: "var(--danger)" }}>failed: {e.data.error}</span>}</div>}
            {["error", "degraded", "budget"].includes(e.kind) && <div className="small" style={{ color: "var(--danger)" }}>{e.data.error ?? e.data.message}</div>}
            {e.kind === "status" && <div className="small">thread status: <strong>{e.data.status}</strong></div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
