"""LLM judge (rubric in agent/prompts/judge.md). Calibrated against the author's own labels in
evals/runners/judge_calibration.py (Cohen's kappa) before any judge score is reported."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agent.llm import structured


class Verdict(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str = ""


async def judge(job_text: str, proposal: str) -> Verdict:
    return await structured("judge", "judge", {"job": job_text[:2500], "proposal": proposal[:4000]}, Verdict, temperature=0)
