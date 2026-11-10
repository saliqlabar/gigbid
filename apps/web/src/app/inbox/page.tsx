import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Me } from "../../App";
import { ActionBadge, FitMeter, RiskBadge, StatusBadge } from "../../components/Badges";
import { Empty, ErrorState, Loading } from "../../components/States";
import { ago, api, money } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

export default function InboxPage({ me }: { me: Me }) {
  const nav = useNavigate();
  const [status, setStatus] = useState("");
  const [minFit, setMinFit] = useState(0);
  const [q, setQ] = useState("");
  const [hideSkipped, setHide] = useState(Boolean(me.settings.filters?.hide_skipped));
  const [msg, setMsg] = useState("");
  const { data, error, loading, reload } = useAsync(
    () => api.GET("/api/jobs", { params: { query: { status, min_fit: minFit, q, hide_skipped: hideSkipped } } }),
    [status, minFit, q, hideSkipped],
  );

  return (
    <div>
      <div className="topbar">
        <div>
          <h1>Job inbox</h1>
          <p className="muted">New jobs ranked by fit. Jobs waiting for your approval come first.</p>
        </div>
        <div className="row">
          <button onClick={reload}>Refresh</button>
          <button className="primary" onClick={async () => {
            const r = await api.POST("/api/jobs/scout");
            setMsg(r.error ? "Could not queue scouting (worker unavailable)" : "Scouting queued: new jobs appear here as they are analysed.");
          }}>Scout now</button>
        </div>
      </div>
      {msg && <p role="status" className="small">{msg}</p>}
      <div className="card row" style={{ marginBottom: "1rem" }} role="search">
        <div style={{ flex: "2 1 200px" }}>
          <label htmlFor="q">Search titles</label>
          <input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. dashboard" />
        </div>
        <div style={{ flex: "1 1 140px" }}>
          <label htmlFor="st">Status</label>
          <select id="st" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="awaiting_approval">Needs approval</option>
            <option value="watching">Watching</option>
            <option value="skipped">Skipped</option>
            <option value="submitted">Submitted</option>
            <option value="blocked">Blocked</option>
            <option value="degraded">Degraded (queued)</option>
            <option value="dead_letter">Failed</option>
          </select>
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <label htmlFor="fit">Minimum fit</label>
          <select id="fit" value={minFit} onChange={(e) => setMinFit(Number(e.target.value))}>
            {[0, 0.2, 0.4, 0.6, 0.8].map((v) => <option key={v} value={v}>{Math.round(v * 100)}%</option>)}
          </select>
        </div>
        <label className="row" style={{ fontWeight: 400, alignSelf: "flex-end" }}>
          <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={hideSkipped} onChange={(e) => setHide(e.target.checked)} />
          Hide skipped
        </label>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {loading && <Loading label="Loading jobs" />}
        {!loading && error ? <ErrorState error={error} onRetry={reload} /> : null}
        {!loading && !error && data?.length === 0 && (
          <Empty title="No jobs yet">Click “Scout now” or wait for the next scheduled scout of your saved searches.</Empty>
        )}
        <ul className="jobs" aria-label="Jobs">
          {data?.map((j) => (
            <li className="job" key={j.thread_id} data-testid="job-row">
              <div>
                <h3>
                  <Link to={j.status === "awaiting_approval" ? `/studio/${j.thread_id}` : `/jobs/${j.thread_id}`}>{j.title}</Link>
                </h3>
                <div className="meta">
                  <StatusBadge status={j.status} />
                  <ActionBadge action={j.action} />
                  <RiskBadge level={j.risk_level} scam={j.scam} injection={j.injection} />
                  <FitMeter value={j.fit} />
                  <span>{money(j.price, j.bid_type)}</span>
                  <span>{j.connects ?? "–"} Connects</span>
                  <span>{j.category}</span>
                  <span>{ago(j.updated)}</span>
                </div>
                {j.reasons?.[0] && <p className="small muted">{j.reasons[0]}</p>}
              </div>
              <div className="actions">
                {j.status === "awaiting_approval" && (
                  <button className="primary" onClick={() => nav(`/studio/${j.thread_id}`)}>Review proposal</button>
                )}
                <button onClick={() => nav(`/jobs/${j.thread_id}`)}>Open</button>
                {!["skipped", "submitted", "rejected"].includes(j.status) && (
                  <button aria-label={`Skip ${j.title}`} onClick={async () => { await api.POST("/api/jobs/{thread_id}/skip", { params: { path: { thread_id: j.thread_id } } }); reload(); }}>
                    Skip
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
