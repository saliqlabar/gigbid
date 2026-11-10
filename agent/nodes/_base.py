"""Node wrapper shared by every graph node (C4, C7, R8).

- Budgets: before running, a thread over `budgets.max_graph_steps` or `budgets.max_tokens_per_job`
  moves to the visible `needs_attention` state instead of running the node.
- Retries + timeout per node (`nodes.<name>.retries/timeout_s` in config, defaults below).
- Failure modes: marketplace down -> `degraded` (worker re-queues the thread later);
  anything else after retries -> `dead_letter` with the error kept for the UI.
- Accounting: tokens / USD / latency per node are added to state (`usage`), and start/end/error events
  go to the run-event log for the live run viewer. LangGraph interrupts pass straight through.
"""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable, Callable

from langgraph.errors import GraphBubbleUp

from agent import trace
from agent.llm import usage_scope
from agent.mcp_client import MarketplaceDown, ToolLayer
from agent.settings import load_config

HALT = ("dead_letter", "needs_attention", "degraded")   # "blocked" still goes to the human (edit or reject)


def budgets() -> dict:
    return load_config()["budgets"]


def node_cfg(name: str) -> dict:
    return load_config().get("nodes", {}).get(name, {})


def halted(state: dict) -> bool:
    return state.get("status") in HALT


def node(name: str) -> Callable:
    def deco(fn: Callable[[dict, ToolLayer], Awaitable[dict]]):
        @functools.wraps(fn)
        async def run(state: dict) -> dict:
            b = budgets()
            tok = trace.current_thread.set((state.get("thread_id", "unknown"), state.get("trace_id")))
            try:
                if state.get("steps", 0) >= b["max_graph_steps"] or state.get("tokens", 0) >= b["max_tokens_per_job"]:
                    msg = (f"{name}: budget exceeded (steps {state.get('steps', 0)}/{b['max_graph_steps']}, "
                           f"tokens {state.get('tokens', 0)}/{b['max_tokens_per_job']})")
                    trace.emit(name, "budget", {"message": msg})
                    return {"status": "needs_attention", "errors": [msg]}
                tools = ToolLayer(state.get("user_id", "local"), state.get("persona", "fullstack"),
                                  state.get("trace_id", "tr_local"))
                cfg = node_cfg(name)
                retries, timeout = int(cfg.get("retries", 1)), float(cfg.get("timeout_s", 300))
                trace.emit(name, "start", {})
                t0 = time.perf_counter()
                out: dict[str, Any] = {}
                with usage_scope() as recs:
                    for attempt in range(retries + 1):
                        try:
                            out = await asyncio.wait_for(fn(state, tools), timeout) or {}
                            break
                        except GraphBubbleUp:
                            raise
                        except MarketplaceDown as e:
                            trace.emit(name, "degraded", {"error": str(e)})
                            out = {"status": "degraded", "errors": [f"{name}: marketplace unavailable: {e}"]}
                            break
                        except Exception as e:  # noqa: BLE001 - recorded, retried, then dead-lettered
                            trace.emit(name, "error", {"attempt": attempt + 1, "error": f"{type(e).__name__}: {e}"})
                            if attempt == retries:
                                out = {"status": "dead_letter",
                                       "errors": [f"{name}: {type(e).__name__}: {e}"]}
                secs = time.perf_counter() - t0
                tokens = sum(r["tokens_in"] + r["tokens_out"] for r in recs)
                cost = sum(r["cost_usd"] for r in recs)
                trace.emit(name, "end", {"latency_s": round(secs, 3), "tokens": tokens, "cost_usd": cost,
                                         "status": out.get("status"), "keys": sorted(out)})
                return {**out, "steps": 1, "tokens": tokens, "cost_usd": cost,
                        "usage": [{"node": name, "latency_s": round(secs, 3), "tokens": tokens, "cost_usd": cost,
                                   "llm_calls": recs}]}
            finally:
                trace.current_thread.reset(tok)
        run.node_name = name
        return run
    return deco
