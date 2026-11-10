"""R&D question A: does tool granularity change agent behaviour? (Part A)

40 tasks with known correct calls, three tool surfaces offered to the same model in one tool-calling turn:
  fine    - many fine-grained tools (one per narrow action, e.g. search_code / search_case_studies / get_job_budget)
  coarse  - three coarse tools (marketplace / portfolio / workspace) taking an `action` + `params`
  best    - GigBid's real MCP tool surface (schemas exported from the three servers), descriptions as shipped
Metrics: tool-selection accuracy (right tool, and right `action` for coarse), argument accuracy (every
expected argument present with the expected value), and tokens per task (prompt + completion, i.e. including
the cost of the tool definitions themselves).

    python -m evals.runners.tool_granularity [--model-tier cheap]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from evals.runners.common import percentile, write_result

# (task, canonical intent, canonical args). Intents map to the expected call in each surface below.
TASKS = [
    ("Find new jobs about Shopify inventory sync.", "search_jobs", {"query": "shopify"}),
    ("Show me the latest data analysis jobs.", "search_jobs", {"category": "Data analysis"}),
    ("Search the job feed for 'power bi dashboard' work.", "search_jobs", {"query": "power bi dashboard"}),
    ("Open job job_00281 so I can read the whole post.", "get_job", {"job_id": "job_00281"}),
    ("What is the budget of job job_00470?", "get_job", {"job_id": "job_00470"}),
    ("Which screening questions does job job_00327 ask?", "get_job", {"job_id": "job_00327"}),
    ("Is client cl_e9820ba5af trustworthy? Check their history.", "get_client_history", {"client_id": "cl_e9820ba5af"}),
    ("How much has client cl_bfc0c55210 spent on the platform?", "get_client_history", {"client_id": "cl_bfc0c55210"}),
    ("What is the average rating of client cl_4c1f0d2a77?", "get_client_history", {"client_id": "cl_4c1f0d2a77"}),
    ("How many Connects do I have left?", "get_connects_balance", {}),
    ("Can I afford to bid this week? Check my Connects.", "get_connects_balance", {}),
    ("What do web development jobs usually pay?", "get_market_stats", {"category": "Web development"}),
    ("Give me price statistics for AI/ML jobs.", "get_market_stats", {"category": "AI/ML"}),
    ("What are the current platform rules for proposals?", "get_policy", {}),
    ("Has the marketplace changed its Connects surcharge? Check the policy.", "get_policy", {}),
    ("Show my conversation thr_85487ef041.", "list_messages", {"thread_id": "thr_85487ef041"}),
    ("Did any client message me? List my threads.", "list_messages", {}),
    ("List all my proposals and their status.", "list_proposals", {}),
    ("Preview (do not send) a fixed-price $400 proposal for job_00281 with this letter: 'You need a fast booking portal; I built one for a clinic and can start with a plan.' Use key draft-0001.",
     "create_proposal_draft", {"job_id": "job_00281", "bid_amount": 400, "dry_run": True}),
    ("Create a proposal draft for job_00470 bidding 30 per hour with idempotency key draft-0002 and the letter 'Your blog needs a clean responsive theme; I shipped similar sites and would start with a wireframe.'",
     "create_proposal_draft", {"job_id": "job_00470", "bid_amount": 30, "bid_type": "hourly"}),
    ("I approved it: submit proposal prop_b22e1dbba5 using idempotency key submit-0001.", "submit_proposal",
     {"proposal_id": "prop_b22e1dbba5"}),
    ("How many Connects would submitting prop_85487ef041 cost? Don't submit, just preview (key submit-0002).",
     "submit_proposal", {"proposal_id": "prop_85487ef041", "dry_run": True}),
    ("Send 'Tuesday 10:00 works for me.' in thread thr_85487ef041 (key msg-00000001).", "send_message",
     {"thread_id": "thr_85487ef041"}),
    ("Reply in thread thr_1234567890 with 'Thanks, I can share a plan tomorrow.' using key msg-00000002.",
     "send_message", {"thread_id": "thr_1234567890"}),
    ("Find portfolio evidence that I have used Celery for background jobs.", "search_evidence", {"requirement": "celery"}),
    ("Do I have experience with Stripe subscriptions? Search my portfolio.", "search_evidence", {"requirement": "stripe"}),
    ("Search my code (not case studies) for WebSocket usage.", "search_evidence", {"requirement": "websocket", "type": "code"}),
    ("Find my past proposals that WON for SaaS dashboard jobs.", "search_evidence",
     {"requirement": "saas dashboard", "type": "proposal", "outcome": "won"}),
    ("What did clients say about my work? Search reviews mentioning communication.", "search_evidence",
     {"requirement": "communication", "type": "review"}),
    ("Have I done React work for a healthcare client?", "search_evidence", {"requirement": "react", "industry": "healthcare"}),
    ("Open the full case study p04.", "get_case_study", {"case_id": "p04"}),
    ("Show me case study p13 in full.", "get_case_study", {"case_id": "p13"}),
    ("Which skills does my portfolio show the most?", "list_skills", {}),
    ("List every skill in my portfolio with project counts.", "list_skills", {}),
    ("When am I free for a 30-minute interview this week?", "get_free_slots", {}),
    ("Find three free interview slots in the next 3 days.", "get_free_slots", {"days_ahead": 3}),
    ("Hold 2026-11-12T10:00:00+05:00 for an interview titled 'Interview: clinic portal' (key hold-00000001).",
     "create_interview_hold", {"start": "2026-11-12T10:00"}),
    ("Put a tentative 45-minute hold at 2026-11-13T14:00:00+05:00 called 'Call with Acme' using key hold-00000002.",
     "create_interview_hold", {"start": "2026-11-13T14:00", "duration_minutes": 45}),
    ("Draft (don't send) an email to client@acme.com with subject 'Interview' saying 'See you Tuesday.' key mail-00000001.",
     "create_email_draft", {"to": "client@acme.com"}),
    ("Save an email draft to hr@example.org titled 'Availability' with body 'I am free Thursday.' key mail-00000002.",
     "create_email_draft", {"to": "hr@example.org"}),
]
assert len(TASKS) == 40

SERVER = {"search_jobs": "marketplace", "get_job": "marketplace", "get_client_history": "marketplace",
          "get_connects_balance": "marketplace", "get_market_stats": "marketplace", "get_policy": "marketplace",
          "list_messages": "marketplace", "list_proposals": "marketplace", "create_proposal_draft": "marketplace",
          "submit_proposal": "marketplace", "send_message": "marketplace", "search_evidence": "portfolio",
          "get_case_study": "portfolio", "list_skills": "portfolio", "get_free_slots": "workspace",
          "create_interview_hold": "workspace", "create_email_draft": "workspace"}


# ------------------------------------------------------------------ surface: best (real MCP schemas)
async def best_tools() -> list[dict]:
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from mcp_servers.marketplace import server as m
    from mcp_servers.portfolio import server as p
    from mcp_servers.workspace import server as w
    out = []
    for mod in (m, p, w):
        async with connect(mod.mcp._mcp_server) as c:
            for t in (await c.list_tools()).tools:
                if t.name in SERVER:
                    out.append({"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                                 "parameters": t.inputSchema}})
    return out


def expect_best(intent: str, args: dict) -> tuple[str, dict]:
    a = dict(args)
    if intent == "search_evidence":
        f = {k: a.pop(k) for k in ("type", "outcome", "industry") if k in a}
        if f:
            a["filters"] = {("types" if k == "type" else "client_industry" if k == "industry" else k):
                            ([v] if k == "type" else v) for k, v in f.items()}
    return intent, a


# ------------------------------------------------------------------ surface: fine-grained
def _fn(name, desc, props, req=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": list(req)}}}


FINE = [
    _fn("search_jobs_by_keyword", "Search open jobs by keywords", {"query": "string"}, ["query"]),
    _fn("search_jobs_by_category", "List open jobs in one category", {"category": "string"}, ["category"]),
    _fn("get_job_description", "Get a job's title and description", {"job_id": "string"}, ["job_id"]),
    _fn("get_job_budget", "Get a job's budget", {"job_id": "string"}, ["job_id"]),
    _fn("get_job_screening_questions", "Get a job's screening questions", {"job_id": "string"}, ["job_id"]),
    _fn("get_client_rating", "Get a client's average rating", {"client_id": "string"}, ["client_id"]),
    _fn("get_client_spend", "Get how much a client has spent", {"client_id": "string"}, ["client_id"]),
    _fn("get_client_history", "Get a client's full history", {"client_id": "string"}, ["client_id"]),
    _fn("get_connects_balance", "Get my Connects balance", {}),
    _fn("get_fixed_price_stats", "Fixed-price quartiles for a category", {"category": "string"}, ["category"]),
    _fn("get_hourly_rate_stats", "Hourly-rate quartiles for a category", {"category": "string"}, ["category"]),
    _fn("get_policy_rules", "Current platform policy rules", {}),
    _fn("get_connects_surcharge", "Current Connects surcharge", {}),
    _fn("list_threads", "List my message threads", {}),
    _fn("get_thread", "Get one message thread", {"thread_id": "string"}, ["thread_id"]),
    _fn("list_proposals", "List my proposals", {}),
    _fn("preview_proposal", "Preview a proposal without sending", {"job_id": "string", "cover_letter": "string", "bid_amount": "number", "bid_type": "string"}, ["job_id", "cover_letter", "bid_amount"]),
    _fn("create_proposal_draft", "Create a proposal draft", {"job_id": "string", "cover_letter": "string", "bid_amount": "number", "bid_type": "string", "idempotency_key": "string"}, ["job_id", "cover_letter", "bid_amount", "idempotency_key"]),
    _fn("preview_submission_cost", "Connects cost of submitting a draft", {"proposal_id": "string"}, ["proposal_id"]),
    _fn("submit_proposal", "Submit a proposal draft", {"proposal_id": "string", "idempotency_key": "string"}, ["proposal_id", "idempotency_key"]),
    _fn("send_message", "Send a message in a thread", {"thread_id": "string", "text": "string", "idempotency_key": "string"}, ["thread_id", "text", "idempotency_key"]),
    _fn("search_case_studies", "Search my case studies", {"query": "string"}, ["query"]),
    _fn("search_code", "Search my repository code", {"query": "string"}, ["query"]),
    _fn("search_readmes", "Search my repository READMEs", {"query": "string"}, ["query"]),
    _fn("search_reviews", "Search my client reviews", {"query": "string"}, ["query"]),
    _fn("search_winning_proposals", "Search my past proposals that won", {"query": "string"}, ["query"]),
    _fn("search_all_proposals", "Search all my past proposals", {"query": "string"}, ["query"]),
    _fn("search_by_industry", "Search my work for clients in an industry", {"query": "string", "industry": "string"}, ["query", "industry"]),
    _fn("get_case_study", "Get one case study", {"case_id": "string"}, ["case_id"]),
    _fn("list_skills", "List my skills", {}),
    _fn("get_free_slots", "Free calendar slots", {"days_ahead": "integer"}),
    _fn("create_interview_hold", "Tentative calendar hold", {"start": "string", "title": "string", "duration_minutes": "integer", "idempotency_key": "string"}, ["start", "title", "idempotency_key"]),
    _fn("create_email_draft", "Save an email draft", {"to": "string", "subject": "string", "body": "string", "idempotency_key": "string"}, ["to", "subject", "body", "idempotency_key"]),
]


def expect_fine(task: str, intent: str, args: dict) -> tuple[set[str], dict]:
    """Fine-grained surfaces often have >1 acceptable tool; any of them counts as correct."""
    a = dict(args)
    t = task.lower()
    if intent == "search_jobs":
        return ({"search_jobs_by_category"} if "category" in a else {"search_jobs_by_keyword"}), a
    if intent == "get_job":
        return ({"get_job_budget"} if "budget" in t else {"get_job_screening_questions"} if "screening" in t
                else {"get_job_description"}), a
    if intent == "get_client_history":
        return ({"get_client_spend", "get_client_history"} if "spent" in t else
                {"get_client_rating", "get_client_history"} if "rating" in t else {"get_client_history"}), a
    if intent == "get_market_stats":
        return {"get_fixed_price_stats", "get_hourly_rate_stats"}, a
    if intent == "get_policy":
        return ({"get_connects_surcharge", "get_policy_rules"} if "surcharge" in t else {"get_policy_rules"}), {}
    if intent == "list_messages":
        return ({"get_thread"} if a else {"list_threads"}), a
    if intent == "create_proposal_draft":
        if a.pop("dry_run", False):
            return {"preview_proposal"}, a
        return {"create_proposal_draft"}, a
    if intent == "submit_proposal":
        if a.pop("dry_run", False):
            return {"preview_submission_cost"}, a
        return {"submit_proposal"}, a
    if intent == "search_evidence":
        q = {"query": a.pop("requirement")}
        if a.get("type") == "code":
            return {"search_code"}, q
        if a.get("type") == "proposal":
            return {"search_winning_proposals"}, q
        if a.get("type") == "review":
            return {"search_reviews"}, q
        if a.get("industry"):
            return {"search_by_industry"}, q | {"industry": a["industry"]}
        return {"search_case_studies", "search_code", "search_readmes"}, q
    return {intent}, a


# ------------------------------------------------------------------ surface: coarse
COARSE = [
    {"type": "function", "function": {"name": f"{srv}", "description": desc, "parameters": {
        "type": "object", "properties": {"action": {"type": "string", "enum": acts},
                                         "params": {"type": "object", "description": "arguments for the action"}},
        "required": ["action"]}}}
    for srv, desc, acts in (
        ("marketplace", "Everything on the freelance marketplace: jobs, clients, Connects, market stats, policy, messages, proposals.",
         [k for k, v in SERVER.items() if v == "marketplace"]),
        ("portfolio", "Everything in my portfolio: evidence search (filters: types, outcome, client_industry), case studies, skills.",
         [k for k, v in SERVER.items() if v == "portfolio"]),
        ("workspace", "Calendar and email: free slots, interview holds, email drafts.",
         [k for k, v in SERVER.items() if v == "workspace"]))
]


def norm(v) -> str:
    return str(v).strip().lower().rstrip("0").rstrip(".") if isinstance(v, (int, float)) else str(v).strip().lower()


def args_match(expected: dict, got: dict) -> bool:
    flat = dict(got)
    for k in ("filters", "params"):
        if isinstance(flat.get(k), dict):
            flat |= flat.pop(k)
    if isinstance(flat.get("types"), list) and flat["types"]:
        flat["type"] = flat["types"][0]
    if "client_industry" in flat:
        flat["industry"] = flat["client_industry"]
    for k, v in expected.items():
        if k == "filters":
            if not args_match(v, flat):
                return False
            continue
        if k == "types":
            if norm(v[0]) != norm(flat.get("type", "")):
                return False
            continue
        g = flat.get(k)
        if g is None:
            return False
        if isinstance(v, bool):
            if bool(g) != v:
                return False
        elif isinstance(v, (int, float)):
            try:
                if abs(float(g) - float(v)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        elif norm(v) not in norm(g):
            return False
    return True


async def call(tools, task, spec) -> tuple[list, int, float]:
    from agent.llm import _client, _params
    t = time.perf_counter()
    r = await _client(spec["provider"], True).chat.completions.create(
        messages=[{"role": "system", "content": "You are GigBid's assistant. Answer by calling exactly one tool."},
                  {"role": "user", "content": task}], tools=tools, **_params(spec, False, 0))
    calls = r.choices[0].message.tool_calls or []
    toks = (r.usage.prompt_tokens + r.usage.completion_tokens) if r.usage else 0
    return calls, toks, time.perf_counter() - t


async def main(a) -> None:
    from agent.llm import model_for
    spec = model_for("tool_study", a.model_tier)
    surfaces = {"fine": FINE, "coarse": COARSE, "best": await best_tools()}
    results, detail = {}, []
    for name, tools in surfaces.items():
        sel = arg = 0
        toks, lat = [], []
        for task, intent, args in TASKS:
            calls, tk, secs = await call(tools, task, spec)
            toks.append(tk)
            lat.append(secs)
            got_name = calls[0].function.name if calls else None
            try:
                got_args = json.loads(calls[0].function.arguments) if calls else {}
            except ValueError:
                got_args = {}
            if name == "fine":
                ok_names, exp = expect_fine(task, intent, args)
                ok_sel = got_name in ok_names
            elif name == "coarse":
                exp = dict(args)
                ok_sel = got_name == SERVER[intent] and got_args.get("action") == intent
                exp = expect_best(intent, exp)[1]
            else:
                exp_name, exp = expect_best(intent, args)
                ok_sel = got_name == exp_name
            ok_arg = ok_sel and args_match(exp, got_args)
            sel += ok_sel
            arg += ok_arg
            detail.append({"surface": name, "task": task, "intent": intent, "got": got_name, "got_args": got_args,
                           "selection_ok": ok_sel, "args_ok": ok_arg, "tokens": tk})
        results[name] = {"tools": len(tools), "tasks": len(TASKS), "tool_selection_accuracy": round(sel / len(TASKS), 3),
                         "argument_accuracy": round(arg / len(TASKS), 3), "tokens_per_task": round(sum(toks) / len(toks)),
                         "latency_p50_s": round(percentile(lat, .5), 2)}
        print(name, results[name], flush=True)
    write_result(f"tool_granularity_{a.model_tier}", {"model": spec["model"], "provider": spec["provider"],
                                                       "results": results, "detail": detail})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-tier", default="cheap", choices=["cheap", "strong"])
    asyncio.run(main(ap.parse_args()))
