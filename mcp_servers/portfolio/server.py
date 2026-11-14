"""Portfolio MCP server (A1, read-only): grounded evidence from a freelancer's portfolio.

Tools: search_evidence(requirement, k, filters), get_case_study(id), get_evidence(id), list_skills().
Resources: portfolio://case/{id}, portfolio://proposal/{id}, portfolio://readme/{project},
           portfolio://code/{project}, portfolio://review/{project}, portfolio://cv
Prompts: answer a screening question, prove a skill, grounded proposal opening, industry experience.

Use it from any MCP host (Claude Desktop, Cursor):
    uvx --from git+https://github.com/saliqlabar/gigbid gigbid-portfolio-mcp      (see mcp_servers/portfolio/README.md)
or  python -m mcp_servers.portfolio.server               (stdio)
    python -m mcp_servers.portfolio.server --http        (Streamable HTTP on :8092/mcp, bearer auth)
Set PORTFOLIO_PERSONA (and PORTFOLIO_ROOT for your own portfolio folder).
"""
from __future__ import annotations

import argparse
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_servers.common.audit import AuditLog
from mcp_servers.common.auth import BearerAuthMiddleware, caller_from_ctx
from mcp_servers.portfolio import prompts, resources, tools

mcp = FastMCP("gigbid-portfolio", instructions=(
    "Read-only access to one freelancer's portfolio: CV, case studies, repo READMEs and code, client "
    "reviews and past proposals with outcomes. Use search_evidence to find proof for a job requirement; "
    "only claim what the returned evidence supports, and cite evidence ids."))
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_audit: AuditLog | None = None


def audit() -> AuditLog:
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit


def _persona(ctx) -> tuple[str, object]:
    c = caller_from_ctx(ctx)
    return tools.persona_of(c.freelancer_id), c


def _log(c, tool: str, args: dict, result, ok: bool = True) -> None:
    audit().append("portfolio", c.user_id, tool, args, result, ok, c.trace_id)


@mcp.tool(annotations=RO)
def search_evidence(
    requirement: Annotated[str, Field(min_length=1, max_length=500,
                                      description="One job requirement, e.g. 'Celery background jobs'")],
    k: Annotated[int, Field(ge=1, le=20)] = 5,
    filters: tools.Filters | None = None,
    ctx: Context | None = None,
) -> tools.EvidenceList:
    """Search the freelancer's OWN portfolio for evidence about one skill or requirement: case studies,
    code, READMEs, client reviews of the freelancer, CV and past proposals. Put the skill or topic in
    `requirement`; narrow with `filters`: types (e.g. ["code"] for code only, ["review"] for what clients
    said, ["proposal"] for past proposals), outcome ("won" / "replied" / "ignored", proposals only) and
    client_industry (e.g. "healthcare"). Each result has type, source, date and (for proposals) outcome,
    plus a portfolio:// URI."""
    persona, c = _persona(ctx)
    res = tools.search_evidence(persona, requirement, k, filters)
    _log(c, "search_evidence", {"requirement": requirement, "k": k}, [r.evidence_id for r in res.results])
    return res


@mcp.tool(annotations=RO)
def get_case_study(case_id: Annotated[str, Field(min_length=1, max_length=80,
                                                 description="Project id (p03) or evidence id")],
                   ctx: Context | None = None) -> tools.CaseStudy:
    """Get one full case study and the ids of its related README, code and review."""
    persona, c = _persona(ctx)
    try:
        res = tools.get_case_study(persona, case_id)
    except KeyError as e:
        raise ToolError(str(e)) from e
    _log(c, "get_case_study", {"case_id": case_id}, res.evidence_id)
    return res


@mcp.tool(annotations=RO)
def get_evidence(evidence_id: Annotated[str, Field(min_length=3, max_length=80)],
                 ctx: Context | None = None) -> tools.Evidence:
    """Get the full text of any evidence item by id (used to verify a cited claim)."""
    persona, c = _persona(ctx)
    try:
        it = tools.get_item(persona, evidence_id)
    except KeyError as e:
        raise ToolError(str(e)) from e
    _log(c, "get_evidence", {"evidence_id": evidence_id}, it.evidence_id)
    return tools.Evidence(evidence_id=it.evidence_id, type=it.type, title=it.title, source=it.source, date=it.date,
                          outcome=it.outcome, project=it.project, client_industry=it.client_industry,
                          skills=it.skills, score=1.0, snippet=it.text[:6000], uri=tools.uri(it.evidence_id))


@mcp.tool(annotations=RO)
def list_skills(ctx: Context | None = None) -> tools.SkillList:
    """All skills in the portfolio with how many projects show each one."""
    persona, c = _persona(ctx)
    res = tools.list_skills(persona)
    _log(c, "list_skills", {}, len(res.skills))
    return res


resources.register(mcp, lambda: tools.persona_of(caller_from_ctx(None).freelancer_id))
prompts.register(mcp)


def http_app():
    from starlette.responses import JSONResponse
    app = mcp.streamable_http_app()

    async def health(request):
        return JSONResponse({"status": "ok", "server": "portfolio"})
    app.add_route("/health", health)
    return BearerAuthMiddleware(app)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8092)
    a = ap.parse_args()
    if a.http:
        import uvicorn
        uvicorn.run(http_app(), host=a.host, port=a.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
