import { useState } from "react";
import { api, errText } from "../../lib/http";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [persona, setPersona] = useState<"fullstack" | "ai_ml" | "data_analyst">("fullstack");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const r =
      mode === "login"
        ? await api.POST("/api/auth/login", { body: { username, password } })
        : await api.POST("/api/auth/register", { body: { username, password, persona } });
    setBusy(false);
    if (r.error || !r.data) return setErr(errText(r.error) || "Sign-in failed");
    localStorage.setItem("gigbid_token", r.data.token);
    location.assign(mode === "register" ? "/onboarding" : "/inbox");
  }

  return (
    <main className="auth">
      <div className="card stack">
        <div className="brand"><i aria-hidden /> GigBid</div>
        <h1>{mode === "login" ? "Sign in" : "Create an account"}</h1>
        <p className="muted">
          A team of agents finds jobs, flags risks and drafts grounded proposals. Nothing is sent without your approval.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="u">Username</label>
          <input id="u" autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} required minLength={3} />
          <label htmlFor="p">Password</label>
          <input id="p" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setP(e.target.value)} required minLength={6} />
          {mode === "register" && (
            <>
              <label htmlFor="persona">Start from a persona</label>
              <select id="persona" value={persona} onChange={(e) => setPersona(e.target.value as typeof persona)}>
                <option value="fullstack">Full-stack web developer (synthetic)</option>
                <option value="ai_ml">AI/ML engineer (synthetic)</option>
                <option value="data_analyst">Data analyst (synthetic)</option>
              </select>
            </>
          )}
          {err && <p role="alert" style={{ color: "var(--danger)" }}>{err}</p>}
          <div className="row" style={{ marginTop: "1rem" }}>
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </button>
            <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
