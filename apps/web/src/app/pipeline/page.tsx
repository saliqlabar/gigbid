import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorState, Loading } from "../../components/States";
import { ago, api, money } from "../../lib/http";
import { useAsync } from "../../lib/useAsync";

const STAGES = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"] as const;

export default function PipelinePage() {
  const { data, error, loading, reload } = useAsync(() => api.GET("/api/pipeline"), []);
  useEffect(() => {
    const id = setInterval(reload, 15000); // cards update from marketplace events
    return () => clearInterval(id);
  }, [reload]);
  if (loading && !data) return <Loading label="Loading pipeline" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <div>
      <div className="topbar">
        <div><h1>Pipeline</h1><p className="muted">Every proposal from draft to outcome. Updates from marketplace events.</p></div>
        <button onClick={reload}>Refresh</button>
      </div>
      {data?.length === 0 ? <Empty title="No proposals yet">Approve a proposal in the studio and it appears here.</Empty> : (
        <div className="kanban" role="list" aria-label="Pipeline board">
          {STAGES.map((s) => {
            const cards = (data ?? []).filter((c) => c.stage === s);
            return (
              <section className="col" key={s} role="listitem" aria-label={`${s}: ${cards.length}`}>
                <h3>{s}<span>{cards.length}</span></h3>
                {cards.map((c) => (
                  <article className="kcard" key={(c.proposal_id ?? "") + (c.thread_id ?? "")}>
                    {c.thread_id ? <Link to={s === "drafted" ? `/studio/${c.thread_id}` : `/jobs/${c.thread_id}`}>{c.title ?? c.job_id}</Link> : <strong>{c.title ?? c.job_id}</strong>}
                    <div className="small muted">{money(c.bid_amount)} · {ago(c.updated)}</div>
                  </article>
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
