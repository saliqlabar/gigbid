import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import AnalyticsPage from "./app/analytics/page";
import InboxPage from "./app/inbox/page";
import JobPage from "./app/jobs/page";
import LoginPage from "./app/login/page";
import MessagesPage from "./app/messages/page";
import OnboardingPage from "./app/onboarding/page";
import PipelinePage from "./app/pipeline/page";
import RunPage from "./app/runs/page";
import SettingsPage from "./app/settings/page";
import StudioPage from "./app/studio/page";
import { StatusBanner } from "./components/StatusBanner";
import { api, type S } from "./lib/http";

export type Me = S["Me"];

function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(0);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    api.GET("/api/approvals").then((r) => setPending(r.data?.length ?? 0));
  }, [loc.pathname]);
  const links: [string, string, number?][] = [
    ["/inbox", "Job inbox", pending],
    ["/pipeline", "Pipeline"],
    ["/messages", "Messages"],
    ["/analytics", "Analytics"],
    ["/settings", "Settings"],
  ];
  return (
    <div className="layout">
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Main navigation">
        <div className="brand"><i aria-hidden /> GigBid</div>
        <nav className="nav">
          {links.map(([to, label, n]) => (
            <NavLink key={to} to={to}>
              {label} {n ? <span className="count" aria-label={`${n} pending approvals`}>{n}</span> : null}
            </NavLink>
          ))}
        </nav>
        <p className="muted small" style={{ margin: "1.5rem .6rem 0" }}>
          Signed in as <strong>{me.username}</strong>
          <br />
          Persona: {String((me.profile as Record<string, unknown>)?.display_name ?? me.persona)}
        </p>
        <button
          style={{ margin: ".5rem .6rem" }}
          onClick={async () => {
            await api.POST("/api/auth/logout");
            localStorage.removeItem("gigbid_token");
            location.assign("/login");
          }}
        >
          Sign out
        </button>
      </aside>
      <main className="main" id="main">
        <button className="menu-btn" aria-label="Open menu" onClick={() => setOpen(true)} style={{ marginBottom: ".75rem" }}>
          ☰ Menu
        </button>
        <StatusBanner />
        {children}
      </main>
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const loc = useLocation();
  useEffect(() => {
    if (loc.pathname.startsWith("/login")) return;
    api.GET("/api/auth/me").then((r) => setMe(r.data ?? null));
  }, [loc.pathname]);

  if (loc.pathname.startsWith("/login")) return <Routes><Route path="/login" element={<LoginPage />} /></Routes>;
  if (me === undefined) return <div className="state" role="status">Loading…</div>;
  if (me === null) return <Navigate to="/login" replace />;
  return (
    <Shell me={me}>
      <Routes>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/onboarding" element={<OnboardingPage me={me} onDone={setMe} />} />
        <Route path="/inbox" element={<InboxPage me={me} />} />
        <Route path="/jobs/:id" element={<JobPage />} />
        <Route path="/studio/:id" element={<StudioPage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/messages" element={<MessagesPage />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<SettingsPage me={me} onChange={setMe} />} />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </Shell>
  );
}
