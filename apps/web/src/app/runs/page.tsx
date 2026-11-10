import { Link, useParams } from "react-router-dom";
import { RunTimeline, type RunEvent } from "../../components/RunTimeline";
import { ErrorState, Loading } from "../../components/States";
import { api } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

export default function RunPage() {
  const { id = "" } = useParams();
  const ev = useAsync(() => api.GET("/api/runs/{thread_id}/events", { params: { path: { thread_id: id } } }), [id]);
  const tr = useAsync(() => api.GET("/api/runs/{thread_id}/trace", { params: { path: { thread_id: id } } }), [id]);
  const hist = useAsync(() => api.GET("/api/runs/{thread_id}/history", { params: { path: { thread_id: id } } }), [id]);
  if (ev.loading) return <Loading label="Loading run" />;
  if (ev.error) return <ErrorState error={ev.error} onRetry={ev.reload} />;
  const t = tr.data as { trace_id?: string; url?: string | null } | undefined;
  return (
    <div className="stack">
      <div className="topbar">
        <div>
          <h1>Agent run</h1>
          <p className="muted small">Thread <code>{id}</code> · trace <code>{t?.trace_id}</code>{" "}
            {t?.url ? <a href={t.url} target="_blank" rel="noreferrer">open full trace</a> : <span>(tracing UI not configured)</span>}</p>
        </div>
        <Link className="btn" to={id.startsWith("conv-") ? "/messages" : `/jobs/${id}`}>Back</Link>
      </div>
      <div className="grid two" style={{ alignItems: "start" }}>
        <section className="card"><h2>Live progress</h2><RunTimeline threadId={id} initial={(ev.data ?? []) as RunEvent[]} /></section>
        <section className="card">
          <h2>Checkpoints</h2>
          <p className="small muted">Every super-step is checkpointed in Postgres. Replay re-runs the nodes after a checkpoint; submissions are idempotent so replays never double-submit.</p>
          <table>
            <thead><tr><th>Step</th><th>Next</th><th>Status</th><th /></tr></thead>
            <tbody>{(hist.data ?? []).map((c) => (
              <tr key={c.checkpoint_id}>
                <td>{c.step}</td><td>{c.next.join(", ") || "–"}</td><td>{c.status}</td>
                <td>{c.next.length > 0 && <button onClick={async () => {
                  await api.POST("/api/runs/{thread_id}/replay/{checkpoint_id}", { params: { path: { thread_id: id, checkpoint_id: c.checkpoint_id } } });
                  hist.reload();
                }}>Replay from here</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
