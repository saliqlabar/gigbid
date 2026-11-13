"""Claim verification rules that do not depend on the model (B3, R5)."""
import asyncio

import agent.llm as llm
from rag import verify


def _fake_out(claims):
    async def structured(node, prompt, variables, model, temperature=0.1):
        return model.model_validate({"claims": claims})
    return structured


def test_partly_supported_without_evidence_is_removed(monkeypatch):
    monkeypatch.setattr(llm, "structured", _fake_out([
        {"n": 1, "status": "supported", "evidence_ids": ["p:case:p01"], "note": ""},
        {"n": 2, "status": "partly_supported", "evidence_ids": [], "note": "not in any evidence"},
    ]))
    text = "I built an invoice extractor [p:case:p01]. I have also managed IP rotation for crawlers."
    v = asyncio.run(verify.verify_text(text, {"p:case:p01": "Invoice field extractor in Python"}))
    assert v.unsupported_before == 1
    assert "IP rotation" not in v.revised_text
    assert "invoice extractor" in v.revised_text
