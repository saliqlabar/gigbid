import { Empty, ErrorState, Loading } from "../../components/States";
import { api, pct } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

function Bars({ rows, value, label, fmt = pct }: { rows: Record<string, any>[]; value: string; label: string; fmt?: (v: number) => string }) {
  const max = Math.max(1e-9, ...rows.map((r) => Number(r[value]) || 0));
  return (
    <div className="bars" role="list">
      {rows.map((r) => (
        <div className="bar" role="listitem" key={r[label]}>
          <span>{r[label]}</span>
          <span className="track"><span style={{ width: `${((Number(r[value]) || 0) / max) * 100}%` }} /></span>
          <span>{r[value] == null ? "–" : fmt(Number(r[value]))}</span>
        </div>
      ))}
    </div>
  );
}

export default function AnalyticsPage() {
  const { data, error, loading, reload } = useAsync(() => api.GET("/api/analytics"), []);
  if (loading) return <Loading label="Loading analytics" />;
  if (error || !data) return <ErrorState error={error} onRetry={reload} />;
  const cl = data.cost_latency as Record<string, any>;
  const cn = data.connects as Record<string, any>;
  return (
    <div className="stack">
      <h1>Analytics</h1>
      <div className="grid three">
        <div className="card"><h3>Connects spent</h3><p style={{ fontSize: "1.6rem", margin: 0 }}>{cn.spent}</p><p className="small muted">{cn.replies_or_better} replies or better · {cn.connects_per_reply ?? "–"} Connects per reply</p></div>
        <div className="card"><h3>Cost per proposal</h3><p style={{ fontSize: "1.6rem", margin: 0 }}>${cl.avg_cost_usd}</p><p className="small muted">{cl.proposals} proposals (local models cost $0)</p></div>
        <div className="card"><h3>Latency per job</h3><p style={{ fontSize: "1.6rem", margin: 0 }}>{cl.p95_latency_s ?? "–"} s</p><p className="small muted">p95 · p50 {cl.p50_latency_s ?? "–"} s</p></div>
      </div>
      <section className="card">
        <h2>Reply and hire rate by category</h2>
        {data.by_category.length === 0 ? <Empty title="No submitted proposals yet" /> : (
          <>
            <h3>Reply rate</h3><Bars rows={data.by_category} value="reply_rate" label="category" />
            <h3>Hire rate</h3><Bars rows={data.by_category} value="hire_rate" label="category" />
          </>
        )}
      </section>
      <div className="grid two">
        <section className="card">
          <h2>Top rejection reasons</h2>
          {data.rejection_reasons.length === 0 ? <p className="muted small">No rejections yet.</p> :
            <Bars rows={data.rejection_reasons} value="count" label="reason" fmt={(v) => String(v)} />}
        </section>
        <section className="card">
          <h2>Decisions</h2>
          <Bars rows={Object.entries(data.decisions).map(([k, v]) => ({ k, v }))} value="v" label="k" fmt={(v) => String(v)} />
        </section>
      </div>
    </div>
  );
}
