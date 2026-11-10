import type { ReactNode } from "react";
import { errText } from "../lib/http";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden /> {label}…
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = errText(error);
  const market = /marketplace unavailable/i.test(msg);
  return (
    <div className="state error" role="alert">
      <strong>{market ? "Marketplace unavailable" : "Something went wrong"}</strong>
      <p>
        {market
          ? "GigBid cannot reach the marketplace right now. Your work is queued and resumes automatically."
          : msg}
      </p>
      {onRetry && <button onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}
