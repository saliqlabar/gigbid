import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Me } from "../../App";
import { api, errText, type S } from "../../lib/http";

type Persona = "fullstack" | "ai_ml" | "data_analyst";

export default function OnboardingPage({ me, onDone }: { me: Me; onDone: (m: Me) => void }) {
  const nav = useNavigate();
  const [persona, setPersona] = useState<Persona>(me.persona as Persona);
  const [rate, setRate] = useState<number | "">(me.settings.hourly_rate_usd ?? (me.profile?.hourly_rate_usd as number) ?? "");
  const [minBudget, setMin] = useState<number | "">(
    me.settings.minimum_project_budget_usd ?? (me.profile?.minimum_project_budget_usd as number) ?? "");
  const [budget, setBudget] = useState(me.settings.weekly_connects_budget ?? 60);
  const [query, setQuery] = useState(me.settings.saved_searches?.[0]?.query ?? "");
  const [market, setMarket] = useState<"gigsim" | "upwork_live">("gigsim");
  const [files, setFiles] = useState<FileList | null>(null);
  const [kind, setKind] = useState<"case" | "cv" | "proposal">("case");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    for (const f of Array.from(files ?? [])) {
      const fd = new FormData();
      fd.append("kind", kind);
      fd.append("file", f);
      const up = await fetch("/api/portfolio/upload", {
        method: "POST", body: fd, credentials: "include",
        headers: { Authorization: `Bearer ${localStorage.getItem("gigbid_token") ?? ""}` },
      });
      if (!up.ok) {
        setBusy(false);
        return setMsg(`Upload failed for ${f.name}: ${(await up.json()).detail}`);
      }
    }
    const settings: S["Settings"] = {
      ...me.settings,
      hourly_rate_usd: rate === "" ? null : Number(rate),
      minimum_project_budget_usd: minBudget === "" ? null : Number(minBudget),
      weekly_connects_budget: budget,
      marketplace: market,
      saved_searches: [{ name: "Main feed", query, category: "", interval_min: market === "upwork_live" ? 60 : 30, limit: 20 }],
    };
    const r = await api.POST("/api/auth/onboarding", { body: { persona, settings } });
    setBusy(false);
    if (r.error || !r.data) return setMsg(errText(r.error));
    onDone(r.data);
    nav("/inbox");
  }

  return (
    <form className="stack" onSubmit={save} aria-labelledby="ob-title">
      <h1 id="ob-title">Set up GigBid</h1>
      <p className="muted">Four steps. You can change all of this later in Settings.</p>

      <section className="card">
        <h2>1. Profile</h2>
        <label htmlFor="persona">Persona</label>
        <select id="persona" value={persona} onChange={(e) => setPersona(e.target.value as Persona)}>
          <option value="fullstack">Full-stack web developer (synthetic persona)</option>
          <option value="ai_ml">AI/ML engineer (synthetic persona)</option>
          <option value="data_analyst">Data analyst (synthetic persona)</option>
        </select>
        <p className="small muted">Your own profile: pick the closest persona and upload your own work in step 3.</p>
      </section>

      <section className="card">
        <h2>2. Connect a marketplace</h2>
        <div className="row">
          <label className="row" style={{ fontWeight: 400 }}>
            <input type="radio" name="m" style={{ width: "auto" }} checked={market === "gigsim"} onChange={() => setMarket("gigsim")} />
            GigSim simulator (connected)
          </label>
          <label className="row" style={{ fontWeight: 400 }}>
            <input type="radio" name="m" style={{ width: "auto" }} checked={market === "upwork_live"} onChange={() => setMarket("upwork_live")} />
            Upwork (official MCP server, OAuth)
          </label>
        </div>
        {market === "upwork_live" && (
          <p className="small">
            <button type="button" onClick={async () => {
              const r = await api.GET("/api/auth/upwork/start");
              const url = (r.data as { authorize_url?: string } | undefined)?.authorize_url;
              if (url) location.assign(url);
              else setMsg(errText(r.error) || "Upwork connected");
            }}>Connect Upwork account</button>{" "}
            Live mode only creates drafts; you confirm every send on Upwork itself.
          </p>
        )}
      </section>

      <section className="card">
        <h2>3. Portfolio</h2>
        <p className="small muted">The selected persona already includes a CV, case studies, repos, reviews and 150 past proposals. Add more work here (.md, .txt or .pdf); it is indexed straight away.</p>
        <div className="grid two">
          <div>
            <label htmlFor="kind">Type</label>
            <select id="kind" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="case">Case study</option>
              <option value="cv">CV</option>
              <option value="proposal">Past proposal</option>
            </select>
          </div>
          <div>
            <label htmlFor="files">Files</label>
            <input id="files" type="file" multiple accept=".md,.txt,.pdf" onChange={(e) => setFiles(e.target.files)} />
          </div>
        </div>
      </section>

      <section className="card">
        <h2>4. Rates, filters and budget</h2>
        <div className="grid three">
          <div>
            <label htmlFor="rate">Hourly rate (USD)</label>
            <input id="rate" type="number" min={1} value={rate} onChange={(e) => setRate(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
          <div>
            <label htmlFor="min">Minimum fixed budget (USD)</label>
            <input id="min" type="number" min={0} value={minBudget} onChange={(e) => setMin(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
          <div>
            <label htmlFor="budget">Weekly Connects budget</label>
            <input id="budget" type="number" min={0} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
          </div>
        </div>
        <label htmlFor="q">Saved search keywords (empty = all new jobs)</label>
        <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. react" />
      </section>

      {msg && <p role="alert">{msg}</p>}
      <button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Finish setup"}</button>
    </form>
  );
}
