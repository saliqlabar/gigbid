"""Resource templates so an MCP host can load full portfolio items."""
from __future__ import annotations

from mcp_servers.portfolio.tools import get_item


def register(mcp, persona_fn) -> None:
    def _read(typ: str, ident: str) -> str:
        p = persona_fn()
        it = get_item(p, f"{p}:{typ}:{ident}")
        head = f"# {it.title}\n\nEvidence id: {it.evidence_id} | type: {it.type} | date: {it.date or '-'}"
        if it.outcome:
            head += f" | outcome: {it.outcome}"
        return f"{head}\n\n{it.text}"

    @mcp.resource("portfolio://case/{case_id}", mime_type="text/markdown",
                  description="A full case study (problem, what I did, result)")
    def case(case_id: str) -> str:
        return _read("case", case_id)

    @mcp.resource("portfolio://proposal/{proposal_id}", mime_type="text/markdown",
                  description="A past proposal with its outcome (won / replied / ignored)")
    def proposal(proposal_id: str) -> str:
        return _read("proposal", proposal_id)

    @mcp.resource("portfolio://readme/{project}", mime_type="text/markdown", description="A repo README")
    def readme(project: str) -> str:
        return _read("readme", project)

    @mcp.resource("portfolio://code/{project}", mime_type="text/plain", description="A repo's source files")
    def code(project: str) -> str:
        return _read("code", project)

    @mcp.resource("portfolio://review/{project}", mime_type="text/markdown", description="A client review")
    def review(project: str) -> str:
        return _read("review", project)

    @mcp.resource("portfolio://cv", mime_type="text/markdown", description="The freelancer's CV")
    def cv() -> str:
        p = persona_fn()
        return get_item(p, f"{p}:cv").text
