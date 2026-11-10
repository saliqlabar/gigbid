import { useState } from "react";
import { money } from "../lib/http";

type Preview = { would_send?: Record<string, any> | null; connects_cost?: number | null; balance_before?: number | null; balance_after?: number | null } | null;

/** Approve / edit / reject, with the marketplace dry_run preview of exactly what will be sent and its cost. */
export function ApprovalPanel({ allowed, preview, editing, busy, onApprove, onSaveEdit, onReject, kind = "proposal" }: {
  allowed: string[]; preview: Preview; editing: boolean; busy: boolean; kind?: "proposal" | "message";
  onApprove: () => void; onSaveEdit: () => void; onReject: (reason: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const send = preview?.would_send ?? {};
  return (
    <section className="card stack" aria-labelledby="ap-title">
      <h2 id="ap-title">Approval</h2>
      <div>
        <h3>Dry run: exactly what will be sent</h3>
        {preview ? (
          <>
            <p className="small">
              {kind === "proposal" ? <>Bid <strong>{money(send.bid_amount, send.bid_type)}</strong> · </> : null}
              Cost <strong>{preview.connects_cost ?? 0} Connects</strong>
              {preview.balance_before != null && <> · balance {preview.balance_before} → {preview.balance_after}</>}
            </p>
            <div className="preview" data-testid="dry-run">{send.cover_letter ?? send.text}</div>
            {(send.screening_answers ?? []).length > 0 && (
              <div className="preview" style={{ marginTop: ".5rem" }}>{(send.screening_answers as string[]).map((a, i) => `Q${i + 1}: ${a}`).join("\n\n")}</div>
            )}
          </>
        ) : <p className="small muted">No preview: the draft is blocked by Compliance. Edit it to continue.</p>}
      </div>
      <div className="row">
        {editing ? (
          <button className="primary" disabled={busy} onClick={onSaveEdit}>Save edit and re-check</button>
        ) : (
          <button className="primary" disabled={busy || !allowed.includes("approve")} onClick={onApprove}
            title={kind === "proposal" ? "Approve: submit this proposal and spend the Connects shown" : "Approve: send this reply"}>
            {kind === "proposal" ? `Approve and submit (${preview?.connects_cost ?? 0} Connects)` : "Approve and send"}
          </button>
        )}
        <button className="danger" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>
      </div>
      {rejecting && (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); onReject(reason); }}>
          <label htmlFor="reason">Why are you rejecting it? (used in analytics)</label>
          <input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3}
            placeholder="e.g. client budget is too low" />
          <div className="row">
            <button type="submit" className="danger" disabled={busy || reason.trim().length < 3}>Confirm reject</button>
            <button type="button" onClick={() => setRejecting(false)}>Cancel</button>
          </div>
        </form>
      )}
      <p className="small muted">GigBid never sends anything without this approval. Edits are diffed, learned by style memory only through its promotion rule, and re-checked by Compliance before you approve again.</p>
    </section>
  );
}
