import { useState } from "react";
import { ApprovalPanel } from "../../components/ApprovalPanel";
import { Empty, ErrorState, Loading } from "../../components/States";
import { api, errText } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

export default function MessagesPage() {
  const { data, error, loading, reload } = useAsync(() => api.GET("/api/messages"), []);
  const [sel, setSel] = useState(0);
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (loading && !data) return <Loading label="Loading messages" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data?.length) return <><h1>Messages</h1><Empty title="No client conversations yet">Client replies to your proposals show up here with a suggested answer.</Empty></>;
  const t = data[Math.min(sel, data.length - 1)];
  const p = t.pending as Record<string, any> | null;

  async function decide(body: { action: "approve" | "edit" | "reject"; edited_text?: string; reason?: string }) {
    setBusy(true);
    const r = await api.POST("/api/approvals/{thread_id}", { params: { path: { thread_id: t.thread_id } }, body });
    setBusy(false);
    setEdit(null);
    setMsg(r.error ? errText(r.error) : r.data?.status === "sent" ? "Reply sent." : `Status: ${r.data?.status}`);
    reload();
  }

  return (
    <div>
      <h1>Messages</h1>
      <div className="grid" style={{ gridTemplateColumns: "minmax(200px, 1fr) 3fr" }}>
        <nav className="card" aria-label="Conversations" style={{ padding: ".5rem" }}>
          {data.map((c, i) => (
            <button key={c.conversation_id} style={{ width: "100%", justifyContent: "space-between", marginBottom: ".35rem" }}
              aria-current={i === sel} onClick={() => { setSel(i); setEdit(null); setMsg(""); }}>
              <span style={{ textAlign: "left" }}>{c.title ?? c.job_id}</span>
              {c.pending && <span className="badge primary">reply ready</span>}
            </button>
          ))}
        </nav>
        <div className="stack">
          <section className="card">
            <h2>{t.title ?? t.job_id} <span className="badge">{t.stage ?? "conversation"}</span></h2>
            <div className="thread">
              {t.messages.map((m: Record<string, any>) => (
                <div key={m.message_id} className={`msg ${m.sender}`}><span className="who">{m.sender} · {String(m.created_at).slice(0, 16).replace("T", " ")}</span>{m.text}</div>
              ))}
            </div>
            {!p && (
              <button style={{ marginTop: ".75rem" }} onClick={async () => {
                const r = await api.POST("/api/messages/{conversation_id}/draft", { params: { path: { conversation_id: t.conversation_id } } });
                setMsg(r.error ? errText(r.error) : "Drafting a reply… refresh in a moment.");
              }}>Draft a reply</button>
            )}
          </section>
          {msg && <p role="status" className="note">{msg}</p>}
          {p && (
            <>
              <section className="card stack">
                <h2>Suggested reply <span className="badge">{p.intent?.intent?.replace("_", " ")}</span></h2>
                {p.intent?.injection_detected && <div className="note blocked">The client message contains instructions aimed at an AI; GigBid ignored them.</div>}
                {edit !== null ? (
                  <textarea aria-label="Edit reply" value={edit} onChange={(e) => setEdit(e.target.value)} />
                ) : (
                  <p style={{ whiteSpace: "pre-wrap" }}>{p.draft?.text}</p>
                )}
                {(p.slots ?? []).length > 0 && <p className="small">Proposed interview slots (from your calendar): {(p.slots as { start: string }[]).map((s) => new Date(s.start).toLocaleString()).join(" · ")}</p>}
                {(p.draft?.needs_human_input ?? []).length > 0 && <div className="note compliance"><strong>You decide:</strong> {(p.draft.needs_human_input as string[]).join("; ")}</div>}
                {edit === null && <button onClick={() => setEdit(p.draft?.text ?? "")}>Edit reply</button>}
              </section>
              <ApprovalPanel kind="message" allowed={p.allowed_actions ?? []} busy={busy} editing={edit !== null}
                preview={{ would_send: { text: edit ?? p.draft?.text }, connects_cost: 0 }}
                onApprove={() => decide({ action: "approve" })}
                onSaveEdit={() => decide({ action: "edit", edited_text: edit ?? "" })}
                onReject={(reason) => decide({ action: "reject", reason })} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
