import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApprovalPanel } from "../../components/ApprovalPanel";
import { RiskBadge } from "../../components/Badges";
import { type Claim, EvidenceHover } from "../../components/EvidenceHover";
import { Empty, ErrorState, Loading } from "../../components/States";
import { api, errText, money } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

export default function StudioPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(
    () => api.GET("/api/approvals/{thread_id}", { params: { path: { thread_id: id } } }), [id]);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [regen, setRegen] = useState<{ i: number; instruction: string }>({ i: 0, instruction: "" });

  if (loading) return <Loading label="Loading proposal" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const p = data?.payload as Record<string, any> | null | undefined;
  if (!p) {
    return (
      <Empty title={`Nothing waiting for approval (${data?.status ?? "unknown"})`}>
        <Link to={`/jobs/${id}`}>Open the job</Link> · <Link to="/inbox">Back to inbox</Link>
      </Empty>
    );
  }
  const draft = p.draft as Record<string, any>;
  const claims = (p.claims ?? []) as Claim[];
  const evidence: Record<string, { title: string; snippet: string; uri?: string }> = {};
  for (const c of claims) for (const e of c.evidence ?? []) evidence[e.evidence_id] = e;
  const critique = p.critique as Record<string, any> | null;
  const compliance = p.compliance as Record<string, any>;
  const removed = claims.filter((c) => c.action === "removed");
  const paragraphs = (draft.cover_letter as string).split(/\n\n+/);

  async function decide(body: { action: "approve" | "edit" | "reject"; edited_text?: string; reason?: string }) {
    setBusy(true);
    setMsg("");
    const r = await api.POST("/api/approvals/{thread_id}", { params: { path: { thread_id: id } }, body });
    setBusy(false);
    if (r.error || !r.data) return setMsg(errText(r.error));
    if (r.data.status === "submitted") { setMsg("Submitted. Connects spent once; the card moves on the pipeline board."); setTimeout(() => nav("/pipeline"), 900); return; }
    if (r.data.status === "rejected") { setMsg("Rejected. Your reason was recorded."); setTimeout(() => nav("/inbox"), 700); return; }
    setEditing(false);
    setMsg(r.data.status === "awaiting_approval" ? "Edit saved and re-checked by Compliance. Review and approve." : `Status: ${r.data.status}`);
    reload();
  }

  return (
    <div className="stack">
      <div className="topbar">
        <div>
          <h1>Proposal studio</h1>
          <p className="muted">{p.job?.title} · <RiskBadge level={p.risk_report?.level} scam={p.risk_report?.scam} injection={p.risk_report?.injection_detected} /> · bid {money(p.price_quote?.amount, p.price_quote?.bid_type)}</p>
        </div>
        <div className="row">
          <Link className="btn" to={`/jobs/${id}`}>Job detail</Link>
          <Link className="btn" to={`/runs/${id}`}>Agent run</Link>
        </div>
      </div>
      {msg && <p role="status" className="note">{msg}</p>}

      <div className="grid two" style={{ alignItems: "start" }}>
        <section className="card stack" aria-labelledby="draft-title">
          <div className="row spread">
            <h2 id="draft-title" style={{ margin: 0 }}>Cover letter</h2>
            {!editing && <button onClick={() => { setText(draft.cover_letter); setEditing(true); }}>Edit draft</button>}
          </div>
          {editing ? (
            <>
              <label htmlFor="editor">Edit (keep [evidence_id] citations next to experience claims)</label>
              <textarea id="editor" value={text} onChange={(e) => setText(e.target.value)} data-testid="editor" />
              <div className="row">
                <label htmlFor="pi" style={{ margin: 0 }}>Regenerate paragraph</label>
                <select id="pi" style={{ width: "auto" }} value={regen.i} onChange={(e) => setRegen({ ...regen, i: Number(e.target.value) })}>
                  {text.split(/\n\n+/).map((_, i) => <option key={i} value={i}>#{i + 1}</option>)}
                </select>
                <input aria-label="Instruction for the regenerated paragraph" style={{ flex: 1, minWidth: 160 }}
                  placeholder="e.g. mention their Shopify store" value={regen.instruction}
                  onChange={(e) => setRegen({ ...regen, instruction: e.target.value })} />
                <button disabled={busy} onClick={async () => {
                  setBusy(true);
                  const r = await api.POST("/api/proposals/{thread_id}/regenerate", { params: { path: { thread_id: id } }, body: { paragraph_index: regen.i, instruction: regen.instruction } });
                  setBusy(false);
                  if (r.data) { const ps = text.split(/\n\n+/); ps[regen.i] = r.data.text; setText(ps.join("\n\n")); } else setMsg(errText(r.error));
                }}>Regenerate</button>
              </div>
              <button onClick={() => setEditing(false)}>Cancel edit</button>
            </>
          ) : (
            <EvidenceHover text={draft.cover_letter} claims={claims} evidence={evidence} />
          )}
          <p className="small muted">Underline: <span className="claim supported">supported</span> · <span className="claim partly_supported">partly supported</span>. Hover or focus a citation chip to see the evidence. {paragraphs.length} paragraph(s), {(draft.cover_letter as string).split(/\s+/).length} words.</p>

          {(draft.screening_answers ?? []).length > 0 && (
            <>
              <h3>Screening answers</h3>
              {(draft.screening_answers as { question: string; answer: string }[]).map((a, i) => (
                <div key={i} className="note"><strong>{a.question}</strong><EvidenceHover text={a.answer} claims={claims} evidence={evidence} /></div>
              ))}
            </>
          )}
        </section>

        <div className="stack">
          <ApprovalPanel allowed={p.allowed_actions ?? []} preview={p.dry_run_preview} editing={editing} busy={busy}
            onApprove={() => decide({ action: "approve" })}
            onSaveEdit={() => decide({ action: "edit", edited_text: text })}
            onReject={(reason) => decide({ action: "reject", reason })} />

          <section className="card">
            <h2>Critic</h2>
            {critique ? (
              <div className="note critic">
                <strong>{critique.approve ? "Approved by the critic" : `Not approved after ${critique.revision + 1} round(s)`}</strong>
                <p className="small">Grounding {critique.grounding}/5 · personalisation {critique.personalisation}/5 · {critique.length_ok ? "length ok" : "too long"} · {critique.generic_opening ? "generic opening" : "specific opening"}</p>
                <ul className="small">{(critique.issues ?? []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
              </div>
            ) : <p className="muted small">No critique.</p>}
            <h2>Compliance</h2>
            {!compliance.allowed && <div className="note blocked" role="alert"><strong>Blocked:</strong> {compliance.blocked_reason}</div>}
            {(compliance.violations ?? []).length === 0 ? <p className="small">No policy issues (policy v{compliance.policy_version}).</p> : (
              <ul className="small">{(compliance.violations as Record<string, string>[]).map((v, i) => <li key={i} className="note compliance"><code>{v.rule}</code> {v.fix}: “{v.excerpt}”</li>)}</ul>
            )}
            {removed.length > 0 && (
              <>
                <h3>Removed by claim verification</h3>
                <ul className="small">{removed.map((c, i) => <li key={i} className="claim unsupported">{c.claim}</li>)}</ul>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
