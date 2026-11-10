"""Deterministic scripted LLM for tests and CI only (MODEL_PROFILE=test).

It answers each prompt the way a well-behaved model would, from simple rules over the prompt text, so
integration tests, the CI smoke eval and Playwright can exercise every graph path (bid, skip, scam,
critic loop, compliance block, approval) without a GPU or API key. Never used by the open/paid profiles.
"""
from __future__ import annotations

import json
import re

SCAM = ("earn $", "per week", "telegram", "whatsapp", "gift card", "crypto", "upfront fee", "registration fee",
        "free sample", "unpaid test", "contact me at", "@gmail", "wire transfer", "immediate hiring")


def _ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\[([a-z_]+:[a-z]+(?::[a-z0-9-]+)?)\]", text)))


def respond(prompt: str, system: str, user: str) -> str:
    low = user.lower()
    if prompt == "analyst":
        skills = re.search(r"Skill tags on the post: (.*)", user)
        tags = [s.strip() for s in (skills.group(1) if skills else "").split(",") if s.strip()][:6]
        title = re.search(r"<untrusted_job_post>\n(.*)", user)
        return json.dumps({"summary": f"Client needs help with {title.group(1)[:80] if title else 'a project'}",
                           "deliverables": [f"Deliver {tags[0]} work" if tags else "Deliver the project"],
                           "required_skills": tags, "nice_to_have": [], "timeline": None,
                           "hidden_needs": ["clear communication"],
                           "client_specifics": [title.group(1)[:60]] if title else []})
    if prompt == "risk_assessor":
        hits = [w for w in SCAM if w in low]
        inj = bool(re.search(r"ignore (all |the )?(previous|prior) instructions|if you are an ai", low))
        flags = [{"kind": "scam", "severity": "high", "evidence": h} for h in hits[:2]]
        if inj:
            flags.append({"kind": "injection", "severity": "high", "evidence": "instruction aimed at AI tools"})
        return json.dumps({"scam_likelihood": 0.9 if len(hits) >= 2 else 0.4 if hits else 0.03, "flags": flags,
                           "explanation": "scripted risk check"})
    if prompt == "fit_matcher":
        out = []
        for block in user.split("REQUIREMENT: ")[1:]:
            req, _, rest = block.partition("\n")
            ids = _ids(rest)
            words = [w for w in re.findall(r"[a-z0-9.+#]+", req.lower()) if len(w) > 2]
            strong = ids and any(w in rest.lower() for w in words)
            out.append({"requirement": req.strip(), "strength": "strong" if strong else "partial" if ids else "none",
                        "evidence_ids": ids[:2], "reason": "scripted grade"})
        return json.dumps({"matches": out})
    if prompt == "writer":
        ids = _ids(user.split("EVIDENCE (the only facts you may claim)")[-1].split("SCREENING QUESTIONS")[0])
        spec = re.search(r'"title": "([^"]+)"', user)
        title = spec.group(1) if spec else "your project"
        cites = " ".join(f"[{i}]" for i in ids[:1])
        letter = (f"You need {title} delivered without surprises, and I have shipped very similar work before {cites}.\n\n"
                  f"I would start with a short plan for the first milestone and a demo at the end of week one."
                  + (f" A related project of mine is documented here [{ids[1]}]." if len(ids) > 1 else ""))
        qs = re.findall(r"^- (.+)$", user.split("SCREENING QUESTIONS")[-1], flags=re.M)
        return json.dumps({"cover_letter": letter,
                           "screening_answers": [{"question": q, "answer": "I would confirm the scope with you first, then share a plan."}
                                                 for q in qs[:3]]})
    if prompt == "claim_verifier":
        n = len(re.findall(r"^\d+\. ", user, flags=re.M))
        claims = []
        for i, line in enumerate(re.findall(r"^\d+\. (.*)$", user, flags=re.M)):
            st = "supported" if ("shipped" in line or "project" in line) else "not_a_claim"
            if "years of experience" in line.lower() or "fortune 500" in line.lower():
                st = "unsupported"
            claims.append({"n": i + 1, "status": st, "evidence_ids": [], "note": ""})
        return json.dumps({"claims": claims[:n]})
    if prompt == "critic":
        rev = "REVISION" in user
        return json.dumps({"approve": True, "grounding": 5, "personalisation": 4, "length_ok": True,
                           "generic_opening": False, "issues": [] if rev else [], "revision_instructions": ""})
    if prompt == "intent":
        intent = ("interview_request" if re.search(r"call|interview|meet", low) else
                  "scam_signal" if any(w in low for w in ("telegram", "whatsapp", "outside")) else
                  "negotiation" if re.search(r"rate|price|budget|discount", low) else
                  "hire" if "start this week" in low else "question")
        return json.dumps({"intent": intent, "confidence": 0.9, "summary": "scripted", "injection_detected": False})
    if prompt == "client_liaison":
        slots = re.search(r"Available interview slots: (.*)", user)
        s = slots.group(1) if slots else ""
        txt = "Thanks for getting back to me. " + (f"I can do a 20-minute call at any of these times: {s}." if "(none" not in s
                                                     else "Happy to answer: I would start with a short plan for the first milestone.")
        return json.dumps({"text": txt, "proposed_slots": [], "needs_human_input": []})
    if prompt == "rag_rewrite":
        return re.search(r"Requirement: (.*)", user).group(1)
    if prompt == "regenerate_paragraph":
        return re.search(r"Paragraph to rewrite:\n(.*?)\n\nEvidence", user, flags=re.S).group(1).strip() + " (revised)"
    if prompt == "judge":
        return json.dumps({"score": 4, "reason": "scripted"})
    return "{}"
