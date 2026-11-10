export function RiskBadge({ level, scam, injection }: { level?: string | null; scam?: boolean | null; injection?: boolean | null }) {
  if (scam) return <span className="badge danger" title="Risk Assessor flagged this as a likely scam">Scam</span>;
  return (
    <>
      <span className={`badge risk-${level ?? "unknown"}`} title="Risk level from the Risk Assessor">
        Risk {level ?? "–"}
      </span>
      {injection && (
        <span className="badge warn" title="Prompt-injection text detected in the post; GigBid ignored it">
          Injection
        </span>
      )}
    </>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const label = status === "awaiting_approval" ? "Needs approval" : status.replace(/_/g, " ");
  const cls =
    status === "awaiting_approval" || status === "blocked"
      ? "primary"
      : status === "submitted" || status === "drafting"
        ? "ok"
        : ["dead_letter", "needs_attention", "degraded"].includes(status)
          ? "warn"
          : "muted";
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function ActionBadge({ action }: { action?: string | null }) {
  if (!action) return null;
  const cls = action === "bid" ? "ok" : action === "watch" ? "neutral" : "muted";
  return <span className={`badge ${cls}`} title="Recommended action">{action}</span>;
}

export function FitMeter({ value }: { value?: number | null }) {
  const v = Math.round((value ?? 0) * 100);
  return (
    <span className="fit" aria-label={`fit ${value == null ? "unknown" : v + " percent"}`} title="Fit score from the Fit Matcher">
      <span className="fit-bar">
        <span style={{ width: `${v}%` }} />
      </span>
      {value == null ? "–" : `${v}%`}
    </span>
  );
}
