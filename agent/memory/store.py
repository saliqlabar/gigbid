"""Long-term memory on top of the LangGraph store. Observations live in the app DB; only
promotion_rules decide what is written to the store (namespaces below)."""
from __future__ import annotations

import json
import time
import uuid

from agent import db
from agent.memory import promotion_rules as pr

_SCHEMA = """create table if not exists memory_observations(id text primary key, user_id text, kind text,
  category text, data text, ts real)"""
_ready = False


def _init():
    global _ready
    if not _ready:
        db.executescript(_SCHEMA)
        _ready = True


def add_observation(user_id: str, kind: str, data: dict, category: str = "") -> str:
    _init()
    oid = f"obs_{uuid.uuid4().hex[:10]}"
    db.execute("insert into memory_observations values(?,?,?,?,?,?)",
               (oid, user_id, kind, category, json.dumps(data), time.time()))
    return oid


def observations(user_id: str, kind: str, category: str | None = None) -> list[dict]:
    _init()
    q = "select * from memory_observations where user_id=? and kind=?" + (" and category=?" if category else "")
    rows = db.query(q + " order by ts desc", (user_id, kind, category) if category else (user_id, kind))
    return [{"id": r["id"], **json.loads(r["data"])} for r in rows]


async def record_edit(store, user_id: str, original: str, edited: str, thread_id: str) -> list[dict]:
    """Observation for one edit, then re-run the style promotion rule. Returns newly promoted rules."""
    sig = pr.style_signals(original, edited)
    add_observation(user_id, "edit", {"thread_id": thread_id, "signals": sig, "words_before": len(original.split()),
                                      "words_after": len(edited.split())})
    promoted = pr.promote_style(observations(user_id, "edit"))
    existing = {i.key for i in await store.asearch(("style", user_id), limit=100)}
    new = []
    for rule in promoted:
        await store.aput(("style", user_id), rule["key"], {**rule, "promoted_at": time.time(),
                                                            "promotion_rule": "style_min_support"})
        if rule["key"] not in existing:
            new.append(rule)
    return new


async def record_outcome(store, user_id: str, category: str, outcome: str, text: str, evidence_ids: list[str],
                         proposal_id: str) -> dict | None:
    add_observation(user_id, "outcome", {"outcome": outcome, "proposal_id": proposal_id,
                                         "features": pr.proposal_features(text, evidence_ids)}, category)
    summary = pr.promote_outcomes(category, observations(user_id, "outcome", category))
    if summary:
        await store.aput(("outcome", user_id, category), "summary", {**summary, "promoted_at": time.time(),
                                                                       "promotion_rule": "outcome_min_outcomes"})
    return summary


async def style_rules(store, user_id: str) -> list[str]:
    return [i.value["rule"] for i in await store.asearch(("style", user_id), limit=20)]


async def outcome_lessons(store, user_id: str, category: str) -> dict | None:
    item = await store.aget(("outcome", user_id, category), "summary")
    return item.value if item else None


async def list_memory(store, user_id: str) -> list[dict]:
    out = []
    for ns in (("style", user_id), ("outcome", user_id)):
        for i in await store.asearch(ns, limit=200):
            out.append({"namespace": list(i.namespace), "key": i.key, "value": i.value})
    return out


async def delete_memory(store, namespace: list[str], key: str, user_id: str) -> None:
    if user_id not in namespace:
        raise PermissionError("memory belongs to another user")
    await store.adelete(tuple(namespace), key)
