"""Pricing Strategist: a bid from market statistics, the client's budget and spend, and the user's
floor; then whether the Connects cost is worth it (expected value). Deterministic and explainable:
every number in `rationale` says where it came from. All constants live in config `pricing`.
"""
from __future__ import annotations

import asyncio

from agent.handoffs import PriceQuote
from agent.marketplace_port import connects_cost
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.settings import load_config


def quote(job: dict, profile: dict, stats: dict, client: dict, fit: float, policy: dict,
          balance: int | None) -> PriceQuote:
    c = load_config()["pricing"]
    rate, floor_fixed = float(profile.get("hourly_rate_usd", 30)), float(profile.get("minimum_project_budget_usd", 200))
    why = []
    if job.get("budget_type") == "hourly":
        lo, hi = job.get("hourly_low"), job.get("hourly_high")
        mlo, mhi = stats.get("hourly_p25"), stats.get("hourly_p75")
        band_lo = max(x for x in (lo, mlo, 0) if x is not None)
        band_hi = max(band_lo, hi or mhi or rate)
        amount = min(max(rate, band_lo), band_hi)
        why.append(f"hourly: my rate ${rate:.0f}; client range {lo}-{hi}; market p25-p75 {mlo}-{mhi}")
        why.append(f"bid ${amount:.0f}/h = my rate clamped into the band ${band_lo:.0f}-${band_hi:.0f}")
        est_total = amount * float(c.get("assumed_hours", 20))
        bid_type = "hourly"
    else:
        bmax = job.get("budget_max")
        if bmax:
            band_lo, band_hi = 0.75 * bmax, bmax
            amount = max(floor_fixed, round(c.get("fixed_fraction_of_budget", 0.9) * bmax))
            why.append(f"fixed: client budget ${bmax:.0f}; bid {c.get('fixed_fraction_of_budget', 0.9):.0%} of it, "
                       f"not below my minimum ${floor_fixed:.0f}")
        else:
            band_lo, band_hi = stats.get("fixed_p25") or floor_fixed, stats.get("fixed_p75") or 2 * floor_fixed
            amount = max(floor_fixed, stats.get("fixed_p50") or floor_fixed)
            why.append(f"fixed, no budget stated: market median ${amount:.0f} (p25-p75 {band_lo}-{band_hi})")
        est_total = amount
        bid_type = "fixed"
    cost = connects_cost(job, policy)
    p_win = c.get("base_win_rate", 0.04) + c.get("fit_win_weight", 0.20) * fit + \
        c.get("hire_rate_weight", 0.10) * float(client.get("hire_rate") or 0.3)
    ev = round(p_win * est_total - cost * float(c.get("usd_per_connect", 0.15)), 2)
    worth = ev > 0 and (balance is None or balance >= cost)
    why.append(f"Connects: cost {cost} (incl. policy surcharge {policy.get('rules', {}).get('connects_surcharge', 0)}), "
               f"balance {balance}; win chance ~{p_win:.0%} -> expected value ${ev}")
    if balance is not None and balance < cost:
        why.append("not enough Connects for this bid")
    return PriceQuote(bid_type=bid_type, amount=round(amount, 2), band_low=round(band_lo, 2), band_high=round(band_hi, 2),
                      connects_cost=cost, connects_balance=balance, connects_worth_it=worth,
                      expected_value_usd=ev, rationale=why)


@node("pricing")
async def pricing(state: dict, tools: ToolLayer) -> dict:
    job = state["job"]
    stats, bal = await asyncio.gather(
        tools.call("pricing", "marketplace.get_market_stats", {"category": job.get("category") or ""}),
        tools.call("pricing", "marketplace.get_connects_balance", {}))
    q = quote(job, state.get("profile") or {}, stats, state.get("client") or {},
              float((state.get("fit_report") or {}).get("score", 0)), state.get("policy") or {}, bal.get("balance"))
    out = {"price_quote": q.model_dump()}
    if not q.connects_worth_it:
        out |= {"status": "watching", "decision": {**state["decision"], "action": "watch",
                                                    "reasons": state["decision"]["reasons"] + q.rationale[-1:],
                                                    "rule": "connects_not_worth_it"}}
    return out
