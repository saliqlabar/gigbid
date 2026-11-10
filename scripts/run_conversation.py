"""Run the conversation graph for one marketplace thread: python scripts/run_conversation.py THREAD_ID"""
import asyncio
import json
import sys

from agent.persistence import open_persistence
from agent.runner import new_conversation_graph, start_conversation


async def main(conv):
    async with open_persistence() as (cp, store):
        g = new_conversation_graph(cp, store)
        r = await start_conversation(g, conv, "reviewer", "fullstack")
        print("STATUS", r["status"])
        if r["interrupts"]:
            p = r["interrupts"][0]
            print("INTENT", p["intent"])
            print("SLOTS", [s["start"] for s in p["slots"]])
            print("DRAFT\n", json.dumps(p["draft"], indent=1))
        print("ERR", r["state"].get("errors"))


asyncio.run(main(sys.argv[1]))
