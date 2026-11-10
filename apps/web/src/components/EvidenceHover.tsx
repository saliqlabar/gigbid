import { useId, useState } from "react";

export type Claim = {
  claim: string;
  status: string;
  evidence_ids: string[];
  note?: string;
  action?: string;
  evidence?: { evidence_id: string; title: string; snippet: string; uri?: string }[];
};

const CITE = /\s*\[([a-z_]+:[a-z]+(?::[a-z0-9-]+)?)\]/g;

/** One citation chip: hover or keyboard-focus shows the evidence behind the claim. */
function Cite({ id, ev }: { id: string; ev?: { title: string; snippet: string; uri?: string } }) {
  const [open, setOpen] = useState(false);
  const pid = useId();
  return (
    <span className="cite" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-describedby={open ? pid : undefined} aria-expanded={open}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setOpen(!open)}>
        {id.split(":").slice(1).join(":")}
      </button>
      {open && (
        <span className="pop" role="tooltip" id={pid}>
          <strong>{ev?.title ?? id}</strong>
          <code className="small">{id}</code>
          <span style={{ display: "block", marginTop: ".35rem" }}>{ev?.snippet ?? "Evidence text not loaded."}</span>
        </span>
      )}
    </span>
  );
}

/** Renders the draft with every claim underlined by its verification status and its citations hoverable. */
export function EvidenceHover({ text, claims, evidence }: {
  text: string; claims: Claim[]; evidence: Record<string, { title: string; snippet: string; uri?: string }>;
}) {
  const byText = claims.map((c) => ({ ...c, plain: c.claim.replace(CITE, "").trim() }));
  const paragraphs = text.split(/\n\n+/);
  return (
    <div className="letter" data-testid="evidence-letter">
      {paragraphs.map((p, pi) => (
        <p key={pi}>
          {p.split(/(?<=[.!?](?:\s*\[[^\]]+\])*)\s+/).map((sent, si) => {
            const plain = sent.replace(CITE, "").trim();
            const c = byText.find((x) => x.plain && (plain.startsWith(x.plain.slice(0, 40)) || x.plain.startsWith(plain.slice(0, 40))));
            const ids = [...sent.matchAll(CITE)].map((m) => m[1]);
            const title = c ? `${c.status.replace(/_/g, " ")}${c.note ? ": " + c.note : ""}` : "not checked";
            return (
              <span key={si}>
                <span className={`claim ${c?.status ?? ""}`} title={title}>{plain}</span>
                {ids.map((id) => <Cite key={id} id={id} ev={evidence[id]} />)}{" "}
              </span>
            );
          })}
        </p>
      ))}
    </div>
  );
}
