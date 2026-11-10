"""Bid graph (C1): one thread per job.

  START -> intake -> (Send, parallel) analyst | risk_assessor | fit_matcher -> decide
        decide: skip/watch -> END ; bid -> pricing -> writer -> verifier -> critic
        critic: revise (max 2) -> writer ; else -> compliance -> approval (interrupt)
        approval: approve -> submitter -> END ; edit -> compliance ; reject -> END
  Any node that hits a budget / dead-letter / degraded state routes to END with that status.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.nodes._base import halted
from agent.nodes.analyst import analyst
from agent.nodes.approval import approval, route_after_approval, submitter
from agent.nodes.compliance import compliance
from agent.nodes.critic import critic, route_after_critic
from agent.nodes.decide import decide
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.pricing import pricing
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.scout import intake
from agent.nodes.verifier import verifier
from agent.nodes.writer import writer
from agent.state import BidState

PARALLEL = ("analyst", "risk_assessor", "fit_matcher")


def fan_out(state: BidState):
    if halted(state):
        return END
    return [Send(n, state) for n in PARALLEL]


def after_decide(state: BidState) -> str:
    if halted(state):
        return END
    return "pricing" if state["decision"]["action"] == "bid" else END


def after(next_node: str):
    def route(state: BidState) -> str:
        if halted(state) or state.get("status") == "watching":
            return END
        return next_node
    route.__name__ = f"to_{next_node}"
    return route


def build_bid_graph(checkpointer=None, store=None):
    g = StateGraph(BidState)
    g.add_node("intake", intake)
    for name, fn in (("analyst", analyst), ("risk_assessor", risk_assessor), ("fit_matcher", fit_matcher)):
        g.add_node(name, fn)
    g.add_node("decide", decide)
    g.add_node("pricing", pricing)
    g.add_node("writer", writer)
    g.add_node("verifier", verifier)
    g.add_node("critic", critic)
    g.add_node("compliance", compliance)
    g.add_node("approval", approval)
    g.add_node("submitter", submitter)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", fan_out, list(PARALLEL) + [END])
    for n in PARALLEL:
        g.add_edge(n, "decide")
    g.add_conditional_edges("decide", after_decide, {"pricing": "pricing", END: END})
    g.add_conditional_edges("pricing", after("writer"), {"writer": "writer", END: END})
    g.add_conditional_edges("writer", after("verifier"), {"verifier": "verifier", END: END})
    g.add_conditional_edges("verifier", after("critic"), {"critic": "critic", END: END})
    g.add_conditional_edges("critic", route_after_critic, {"writer": "writer", "compliance": "compliance", "end": END})
    g.add_conditional_edges("compliance", after("approval"), {"approval": "approval", END: END})
    g.add_conditional_edges("approval", route_after_approval,
                            {"submitter": "submitter", "compliance": "compliance", "approval": "approval", "end": END})
    g.add_edge("submitter", END)
    return g.compile(checkpointer=checkpointer, store=store)
