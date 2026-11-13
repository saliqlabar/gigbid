import { useState } from "react";
import type { Me } from "../../App";
import { ErrorState, Loading } from "../../components/States";
import { api, errText, type S } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

export default function SettingsPage({ me, onChange }: { me: Me; onChange: (m: Me) => void }) {
  const [s, setS] = useState<S["Settings"]>(me.settings);
  const [msg, setMsg] = useState("");
  const mem = useAsync(() => api.GET("/api/memory"), []);
  const searches = s.saved_searches ?? [];

  async function save() {
    const r = await api.PUT("/api/settings", { body: s });
    if (r.data) { onChange(r.data); setS(r.data.settings); setMsg("Saved."); } else setMsg(errText(r.error));
  }

  return (
    <div className="stack">
      <h1>Settings</h1>
      <section className="card">
        <h2>Autonomy</h2>
        <label htmlFor="aut">What may GigBid do without asking?</label>
        <select id="aut" value={s.autonomy} onChange={(e) => setS({ ...s, autonomy: e.target.value as S["Settings"]["autonomy"] })}>
          <option value="manual">Nothing: I review every recommendation</option>
          <option value="auto_skip_low_fit">Auto-skip low-fit and duplicate jobs</option>
        </select>
        <p className="small muted">There is no auto-send setting: every proposal, message and Connects spend needs your approval.</p>
      </section>
      <section className="card">
        <h2>Agency mode</h2>
        <label>
          <input type="checkbox" checked={!!s.agency_mode} onChange={(e) => setS({ ...s, agency_mode: e.target.checked })} />{" "}
          Route each job to the best-fit team member (AI/ML, full-stack, data analyst) and draft in their voice
        </label>
        <p className="small muted">The job detail page shows each member's fit score and why the job went to them.</p>
      </section>
      <section className="card">
        <h2>Budgets</h2>
        <div className="grid three">
          <div><label htmlFor="wc">Weekly Connects budget</label><input id="wc" type="number" min={0} value={s.weekly_connects_budget ?? 0} onChange={(e) => setS({ ...s, weekly_connects_budget: Number(e.target.value) })} /></div>
          <div><label htmlFor="hr">Hourly rate (USD)</label><input id="hr" type="number" value={s.hourly_rate_usd ?? ""} onChange={(e) => setS({ ...s, hourly_rate_usd: e.target.value ? Number(e.target.value) : null })} /></div>
          <div><label htmlFor="mb">Minimum fixed budget</label><input id="mb" type="number" value={s.minimum_project_budget_usd ?? ""} onChange={(e) => setS({ ...s, minimum_project_budget_usd: e.target.value ? Number(e.target.value) : null })} /></div>
        </div>
      </section>
      <section className="card">
        <h2>Saved searches</h2>
        <table>
          <thead><tr><th>Name</th><th>Keywords</th><th>Every (min)</th><th /></tr></thead>
          <tbody>{searches.map((x, i) => (
            <tr key={i}>
              <td><input aria-label="Search name" value={x.name} onChange={(e) => { const n = [...searches]; n[i] = { ...x, name: e.target.value }; setS({ ...s, saved_searches: n }); }} /></td>
              <td><input aria-label="Keywords" value={x.query ?? ""} onChange={(e) => { const n = [...searches]; n[i] = { ...x, query: e.target.value }; setS({ ...s, saved_searches: n }); }} /></td>
              <td><input aria-label="Interval in minutes" type="number" min={15} value={x.interval_min ?? 30} onChange={(e) => { const n = [...searches]; n[i] = { ...x, interval_min: Number(e.target.value) }; setS({ ...s, saved_searches: n }); }} /></td>
              <td><button aria-label={`Remove ${x.name}`} onClick={() => setS({ ...s, saved_searches: searches.filter((_, j) => j !== i) })}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table>
        <button onClick={() => setS({ ...s, saved_searches: [...searches, { name: `Search ${searches.length + 1}`, query: "", category: "", interval_min: 30, limit: 20 }] })}>Add saved search</button>
        <p className="small muted">Live Upwork mode never polls more often than every 60 minutes (docs/RUNBOOK.md).</p>
      </section>
      <section className="card">
        <h2>Connected accounts</h2>
        <p>GigSim: <span className="badge ok">connected</span> · Upwork: {me.connected?.upwork ? <span className="badge ok">connected</span> : <span className="badge muted">not connected</span>}</p>
        <label htmlFor="mk">Marketplace</label>
        <select id="mk" value={s.marketplace} onChange={(e) => setS({ ...s, marketplace: e.target.value as S["Settings"]["marketplace"] })}>
          <option value="gigsim">GigSim (simulator)</option>
          <option value="upwork_live">Upwork live (drafts only)</option>
        </select>
      </section>
      <div className="row"><button className="primary" onClick={save}>Save settings</button>{msg && <span role="status">{msg}</span>}</div>

      <section className="card" aria-labelledby="mem-title">
        <h2 id="mem-title">What GigBid has learned</h2>
        <p className="small muted">Only promoted knowledge lives here: a style rule needs the same change in 3 of your last 5 edits; an outcome lesson needs at least 3 outcomes in a category. Delete anything you disagree with.</p>
        {mem.loading ? <Loading /> : mem.error ? <ErrorState error={mem.error} /> : (mem.data ?? []).length === 0 ? <p className="muted">Nothing learned yet.</p> : (
          <table>
            <thead><tr><th>Memory</th><th>Learned</th><th>Evidence</th><th /></tr></thead>
            <tbody>{(mem.data ?? []).map((m) => {
              const v = m.value as Record<string, any>;
              return (
                <tr key={m.namespace.join("/") + m.key} data-testid="memory-row">
                  <td>{m.namespace[0]}</td>
                  <td>{v.rule ?? `${v.category}: ${pct2(v.reply_or_better_rate)} reply-or-better (n=${v.n}). ${(v.lessons ?? []).join("; ")}`}</td>
                  <td className="small">{v.support ? `${v.support} of last ${v.window} edits` : `${v.n} outcomes`} · rule <code>{v.promotion_rule}</code></td>
                  <td><button className="danger" aria-label="Delete memory" onClick={async () => {
                    await api.DELETE("/api/memory", { params: { query: { namespace: m.namespace.join("/"), key: m.key } } });
                    mem.reload();
                  }}>Delete</button></td>
                </tr>
              );
            })}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function pct2(v?: number) { return v == null ? "–" : `${Math.round(v * 100)}%`; }
