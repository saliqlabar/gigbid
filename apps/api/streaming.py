"""SSE bridge to LangGraph runs. The worker consumes `graph.astream(stream_mode="updates")` and writes
each node event to the run-event log; this endpoint tails that log for one thread and pushes every
event to the browser as Server-Sent Events, so the run viewer updates node by node."""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from agent import trace


async def stream_thread(request: Request, thread_id: str, after: float = 0.0, idle_timeout_s: float = 600):
    async def gen():
        last, idle = after, 0.0
        while not await request.is_disconnected():
            evs = trace.events(thread_id, last)
            for e in evs:
                last = e["ts"]
                yield {"event": "node", "data": json.dumps({k: e[k] for k in ("ts", "node", "kind", "data", "trace_id")})}
            idle = 0.0 if evs else idle + 0.5
            if idle > idle_timeout_s:
                break
            if not evs:
                yield {"event": "ping", "data": "{}"} if idle % 15 == 0 else {"comment": "keepalive"}
            await asyncio.sleep(0.5)
    return EventSourceResponse(gen())
