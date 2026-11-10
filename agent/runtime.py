"""Process-wide handles the graph nodes need but that must not live in checkpointed state:
the long-term store (set by whoever compiles the graph: worker, API, eval runner)."""
from __future__ import annotations

_store = None


def set_store(store) -> None:
    global _store
    _store = store


def store():
    if _store is None:
        from langgraph.store.memory import InMemoryStore
        set_store(InMemoryStore())
    return _store


async def memory_for(state: dict) -> dict:
    from agent.memory.store import outcome_lessons, style_rules
    s, user = store(), state.get("user_id", "local")
    return {"style_rules": await style_rules(s, user),
            "outcome": await outcome_lessons(s, user, (state.get("job") or {}).get("category") or "")}
