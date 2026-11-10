"""Conversation graph (C1): one thread per client conversation.

  START -> read_messages -> classify -> (interview_request|hire) offer_slots -> draft_reply
        -> approve_reply (interrupt) -> approve: send_reply -> END ; edit: approve_reply again ; reject: END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.nodes._base import halted
from agent.nodes.client_liaison import (approve_reply, classify, draft_reply, offer_slots, read_messages,
                                        send_reply)
from agent.state import ConversationState


def _after_read(s):
    if halted(s):
        return END
    return "classify" if any(m["sender"] == "client" for m in s.get("messages", [])) else END


def _after_classify(s):
    if halted(s):
        return END
    return "offer_slots" if s["intent"]["intent"] in ("interview_request", "hire") else "draft_reply"


def _after_draft(s):
    return END if halted(s) else "approve_reply"


def _after_approval(s):
    if halted(s):
        return END
    return {"approved": "send_reply", "edited": "approve_reply", "blocked": "approve_reply"}.get(s.get("status"), END)


def build_conversation_graph(checkpointer=None, store=None):
    g = StateGraph(ConversationState)
    for name, fn in (("read_messages", read_messages), ("classify", classify), ("offer_slots", offer_slots),
                     ("draft_reply", draft_reply), ("approve_reply", approve_reply), ("send_reply", send_reply)):
        g.add_node(name, fn)
    g.add_edge(START, "read_messages")
    g.add_conditional_edges("read_messages", _after_read, {"classify": "classify", END: END})
    g.add_conditional_edges("classify", _after_classify, {"offer_slots": "offer_slots", "draft_reply": "draft_reply", END: END})
    g.add_edge("offer_slots", "draft_reply")
    g.add_conditional_edges("draft_reply", _after_draft, {"approve_reply": "approve_reply", END: END})
    g.add_conditional_edges("approve_reply", _after_approval,
                            {"send_reply": "send_reply", "approve_reply": "approve_reply", END: END})
    g.add_edge("send_reply", END)
    return g.compile(checkpointer=checkpointer, store=store)
