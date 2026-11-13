"""Job Analyst: job post -> JobSpec (deliverables, skills, budget, timeline, screening questions,
hidden needs, client specifics). No MCP access: it works only on the post it is handed.

Facts the marketplace already states (budget, screening questions, skill tags) are copied from the
structured job record, not re-extracted, so the LLM cannot hallucinate them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from agent.handoffs import JobSpec
from agent.llm import structured
from agent.mcp_client import ToolLayer
from agent.nodes._base import node
from agent.safety.injection import delimit, strip


class _SpecOut(BaseModel):
    summary: str = Field("", description="one sentence: what the client actually needs")
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    timeline: str | None = None
    hidden_needs: list[str] = Field(default_factory=list, description="needs implied but not stated")
    client_specifics: list[str] = Field(default_factory=list, description="details unique to this client")


def budget_text(job: dict) -> str:
    if job.get("budget_type") == "hourly":
        return f"hourly {job.get('hourly_low')}-{job.get('hourly_high')} USD"
    return f"fixed {job.get('budget_min') or ''}-{job.get('budget_max') or 'unknown'} USD"


async def analyse(job: dict) -> JobSpec:
    out = await structured("analyst", "analyst", {
        "job_id": job["job_id"], "skills": ", ".join(job.get("skills", [])), "budget": budget_text(job),
        "job_post": delimit(strip(f"{job['title']}\n\n{job.get('description', '')}")[0], "job_post")}, _SpecOut)
    # trusted structured fields come from the marketplace record, never from the model
    spec = JobSpec(job_id=job["job_id"], title=job["title"], **out.model_dump())
    spec.budget_type = job.get("budget_type") or "unknown"
    if spec.budget_type == "hourly":
        spec.budget_min, spec.budget_max = job.get("hourly_low"), job.get("hourly_high")
    else:
        spec.budget_min, spec.budget_max = job.get("budget_min"), job.get("budget_max")
    spec.screening_questions = list(job.get("screening_questions") or [])
    return spec


@node("analyst")
async def analyst(state: dict, tools: ToolLayer) -> dict:
    spec = await analyse(state["job"])
    return {"job_spec": spec.model_dump(), "analyses": {"analyst": "ok"}}
