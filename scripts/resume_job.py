"""Resume a thread waiting for approval: python scripts/resume_job.py THREAD approve|reject|edit [text]"""
import asyncio
import json
import sys

from agent.persistence import open_persistence
from agent.runner import new_graph, resume


async def main(thread, action, extra):
    async with open_persistence() as (cp, store):
        g = new_graph(cp, store)
        dec = {"action": action}
        if action == "reject":
            dec["reason"] = extra or "not a fit"
        if action == "edit":
            dec["edited_text"] = extra
        r = await resume(g, thread, "reviewer", dec)
        print(r["status"], json.dumps(r["state"].get("submission"), indent=1))


asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
