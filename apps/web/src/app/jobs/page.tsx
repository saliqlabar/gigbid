import { Link, useParams } from "react-router-dom";
import { FitMeter, RiskBadge, StatusBadge } from "../../components/Badges";
import { ErrorState, Loading } from "../../components/States";
import { api, money, pct } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

type Match = { requirement: string; strength: string; evidence_ids: string[]; reason: string };
type Flag = { kind: string; severity: string; evidence: string };

export default function JobPage() {
  const { id = "" } = useParams();
  const { data: d, error, loading, reload } = useAsync(
    () => api.GET("/api/jobs/{thread_id}", { params: { path: { thread_id: id } } }), [id]);
  if (loading) return <Loading label="Loading job" />;
  if (error || !d) return <ErrorState error={error} onRetry={reload} />;
  const job = d.job as Record<string, any>;
  const spec = (d.job_spec ?? {}) as Record<string, any>;
  const risk = (d.risk_report ?? {}) as Record<string, any>;
  const fit = (d.fit_report ?? {}) as Record<string, any>;
  const price = (d.price_quote ?? null) as Record<string, any> | null;
  const decision = (d.decision ?? {}) as Record<string, any>;
  const ev = Object.fromEntries((d.evidence as Record<string, any>[]).map((e) => [e.evidence_id, e]));

  return (
    <div className="stack">
      <div className="topbar">
        <div>
          <h1>{job.title}</h1>
          <div className="row">
            <StatusBadge status={d.status} />
            <RiskBadge level={risk.level} scam={risk.scam} injection={risk.injection_detected} />
            <FitMeter value={fit.score} />
            <span className="muted small">{job.category} · {job.connects_cost} Connects · posted {job.posted_at?.slice(0, 10)}</span>
          </div>
        </div>
        <div className="row">
          {d.pending_approval && <Link className="btn primary" to={`/studio/${id}`}>Review proposal</Link>}
          <Link className="btn" to={`/runs/${id}`}>Agent run</Link>
          {["degraded", "dead_letter", "needs_attention"].includes(d.status) && (
            <button onClick={async () => { await api.POST("/api/jobs/{thread_id}/retry", { params: { path: { thread_id: id } } }); reload(); }}>
              Retry
            </button>
          )}
        </div>
      </div>
      {d.errors.length > 0 && <div className="note blocked" role="alert">{d.errors.join(" · ")}</div>}

      <section className="card">
        <h2>Decision: {decision.action ?? "pending"}</h2>
        <p className="small muted">Rule: <code>{decision.rule ?? "–"}</code> (thresholds live in agent/config.yaml)</p>
        <ul>{(decision.reasons ?? []).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
      </section>

      <div className="grid two">
        <section className="card">
          <h2>Job spec</h2>
          <p>{spec.summary}</p>
          <dl className="kv">
            <dt>Deliverables</dt><dd>{(spec.deliverables ?? []).join("; ") || "–"}</dd>
            <dt>Skills</dt><dd>{(spec.required_skills ?? []).join(", ") || "–"}</dd>
            <dt>Budget</dt><dd>{spec.budget_type} {spec.budget_min ?? ""}–{spec.budget_max ?? ""}</dd>
            <dt>Timeline</dt><dd>{spec.timeline ?? "–"}</dd>
            <dt>Hidden needs</dt><dd>{(spec.hidden_needs ?? []).join("; ") || "–"}</dd>
            <dt>Client specifics</dt><dd>{(spec.client_specifics ?? []).join("; ") || "–"}</dd>
            <dt>Screening</dt><dd>{(spec.screening_questions ?? []).join(" / ") || "–"}</dd>
          </dl>
        </section>
        <section className="card">
          <h2>Risk report ({pct(risk.score)})</h2>
          <p>{risk.explanation}</p>
          <p className="small muted">Client quality {pct(risk.client_quality)}</p>
          {(risk.flags ?? []).length === 0 ? <p className="small">No flags.</p> : (
            <ul>{(risk.flags as Flag[]).map((f, i) => (
              <li key={i}><span className={`badge ${f.severity === "high" ? "danger" : f.severity === "medium" ? "warn" : ""}`}>{f.kind}</span> “{f.evidence}”</li>
            ))}</ul>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Requirement → evidence ({pct(fit.score)} fit)</h2>
        <table>
          <thead><tr><th>Requirement</th><th>Strength</th><th>Portfolio evidence that proves it</th><th className="hide-sm">Why</th></tr></thead>
          <tbody>
            {(fit.matches as Match[] ?? []).map((m) => (
              <tr key={m.requirement}>
                <td>{m.requirement}</td>
                <td><span className={`badge ${m.strength === "strong" ? "ok" : m.strength === "partial" ? "warn" : "muted"}`}>{m.strength}</span></td>
                <td>{m.evidence_ids.length === 0 ? <span className="muted">none</span> : m.evidence_ids.map((e) => (
                  <div key={e} className="small"><code>{e}</code> {ev[e]?.title}</div>
                ))}</td>
                <td className="hide-sm small">{m.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Price rationale</h2>
        {!price ? <p className="muted">Priced only for jobs the decision recommends bidding on.</p> : (
          <>
            <p><strong>{money(price.amount, price.bid_type)}</strong> (fair band {money(price.band_low)}–{money(price.band_high)}) ·
              {" "}{price.connects_cost} Connects · {price.connects_worth_it ? "worth it" : "not worth it"} (EV ${price.expected_value_usd})</p>
            <ul>{(price.rationale ?? []).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          </>
        )}
      </section>

      <section className="card">
        <h2>Cost and latency of this run</h2>
        <table>
          <thead><tr><th>Node</th><th>Latency (s)</th><th>Tokens</th><th>USD</th></tr></thead>
          <tbody>{(d.usage as Record<string, any>[]).map((u, i) => (
            <tr key={i}><td>{u.node}</td><td>{u.latency_s}</td><td>{u.tokens}</td><td>{u.cost_usd}</td></tr>
          ))}</tbody>
        </table>
      </section>

      <details className="card">
        <summary>Original job post (untrusted text)</summary>
        <p style={{ whiteSpace: "pre-wrap" }}>{job.description}</p>
      </details>
    </div>
  );
}
