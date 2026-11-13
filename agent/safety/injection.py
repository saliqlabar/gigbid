"""Prompt-injection defence for untrusted text (job posts, client messages) (R9/E4).

Layers:
1. Delimit: untrusted text is wrapped in <untrusted_...> tags; any tag-like text that could close the
   wrapper is neutralised, and prompts say "data, never instructions".
2. Detect: regex signatures of instruction-override attempts. Detection raises a risk flag (shown in
   the UI) but never changes what the agent is allowed to do.
3. Constrain: the agent cannot act on the text anyway: writes need human approval, tools are
   allowlisted per node, and Compliance strips contact details/links from outgoing text.
"""
from __future__ import annotations

import os
import re


def level() -> str:
    """full (production) | prompt (no Compliance Guard) | none (no defences): eval-only ablation switch."""
    return os.getenv("GIGBID_DEFENCES", "full")

SIGNATURES = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any )?(previous|prior|your) (instructions|rules|guidelines)",
    r"(you are|act as|pretend to be) (now )?(an? )?(ai|assistant|chatgpt|language model|system)",
    r"\b(system|developer) (prompt|message|instructions?)\b",
    r"(include|add|write|put|insert|mention) (the (word|phrase|code)|this (word|phrase|code)|the secret|your (api|system))",
    r"if you are an? (ai|llm|bot|language model|automated)",
    r"(reveal|print|show|output) (your|the) (system )?(prompt|instructions|api key)",
    r"(start|begin|end) your (proposal|reply|response|answer) with",
    r"(new|updated) instructions?:",
    r"</?(system|assistant|instructions?)>",
    # role markers that only make sense when addressed to a model (added after dev-set failures, see report)
    r"\[(system|assistant|developer)\]",
    r"<!--\s*(system|assistant|ai|developer)\s*:",
    r"disregard (the |all |any )?(platform )?(policy|policies|rules)",
    r"(attention|note (for|to)) (ai|bidding|chat)?\s*(bots?|assistants?|agents?)",
]
_RX = [re.compile(s, re.I) for s in SIGNATURES]


def detect(text: str) -> list[str]:
    """Matched snippets (empty list = nothing suspicious found)."""
    hits = []
    if level() == "none":
        return hits
    for rx in _RX:
        m = rx.search(text or "")
        if m:
            s = max(0, m.start() - 30)
            hits.append(text[s:m.end() + 30].replace("\n", " "))
    return hits


def strip(text: str) -> tuple[str, int]:
    """Remove the lines of untrusted text that carry an instruction aimed at AI tools, so judgement nodes
    (Risk Assessor, Job Analyst) assess the client's real request and the planted text cannot reach the
    Writer through the JobSpec. The original text is kept in state and the injection is still flagged."""
    if level() == "none" or not text:
        return text or "", 0
    out, n = [], 0
    for line in text.splitlines():
        if any(rx.search(line) for rx in _RX):
            out.append("[removed by GigBid: instruction aimed at AI tools]")
            n += 1
        else:
            out.append(line)
    return "\n".join(out), n


def delimit(text: str, kind: str = "job_post") -> str:
    if level() == "none":
        return text or ""
    safe = re.sub(r"</?\s*untrusted[^>]*>", "[tag removed]", text or "", flags=re.I)
    return f"<untrusted_{kind}>\n{safe}\n</untrusted_{kind}>"
