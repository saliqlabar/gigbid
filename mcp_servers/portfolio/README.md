# gigbid-portfolio-mcp

A read-only MCP server that lets any MCP host (Claude Desktop, Cursor, your own agent) answer
"what have I actually done?" from a freelancer's portfolio, with evidence: CV, case studies, repo READMEs
and code, client reviews and past proposals with their outcomes. It is the Portfolio server inside
[GigBid](../../README.md), published on its own.

## Install

No clone needed (Python 3.11+ and [uv](https://docs.astral.sh/uv/)):

```bash
uvx --from git+https://github.com/saliqlabar/gigbid gigbid-portfolio-mcp
```

Claude Desktop / Cursor (`claude_desktop_config.json` or `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "portfolio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/saliqlabar/gigbid", "gigbid-portfolio-mcp"],
      "env": {"PORTFOLIO_PERSONA": "fullstack"}
    }
  }
}
```

Docker (Streamable HTTP on :8092/mcp, bearer-token auth), from a clone:

```bash
docker build -f infra/docker/portfolio-mcp.Dockerfile -t gigbid-portfolio-mcp .
docker run --rm -p 8092:8092 -e MCP_TOKENS="my-secret=me:fullstack" gigbid-portfolio-mcp
# client sends: Authorization: Bearer my-secret
```

## Use your own portfolio

Three synthetic demo portfolios ship with the package (`ai_ml`, `fullstack`, `data_analyst`). To serve yours,
copy one of them as a template and point the server at the folder:

```
my-portfolio/
  me/
    manifest.json      # [{"project": "p01", "name": ..., "skills": [...], "date": ..., "industry": ...}]
    profile.json       # headline, rate, skills
    cv.pdf             # or cv.md
    case_studies/p01.md
    repos/p01/README.md + code files
    reviews.json       # [{"evidence_id", "project", "text", "rating", "date", "client_industry"}]
    proposals.json     # [{"evidence_id", "text", "outcome": "won|replied|ignored", "job_category", ...}]
```

```bash
PORTFOLIO_ROOT=/path/to/my-portfolio PORTFOLIO_PERSONA=me uvx --from git+https://github.com/saliqlabar/gigbid gigbid-portfolio-mcp
```

Embeddings (`nomic-embed-text-v1.5`) and the reranker run locally on CPU; nothing leaves your machine.

## What it exposes

| Kind | Name | What for |
|---|---|---|
| tool | `search_evidence(requirement, k, filters)` | evidence for one requirement; filters: `types` (case, code, readme, review, cv, proposal), `outcome` (won / replied / ignored), `client_industry`, `project`, `since` |
| tool | `get_case_study(case_id)` | one full case study plus the ids of its README, code and review |
| tool | `get_evidence(evidence_id)` | one evidence item in full |
| tool | `list_skills()` | every skill with how many projects show it |
| resource | `portfolio://case/{case_id}`, `portfolio://proposal/{proposal_id}`, `portfolio://readme/{project}`, `portfolio://code/{project}`, `portfolio://review/{project}`, `portfolio://cv` | load full items into the host |
| prompt | answer a screening question, prove a skill, grounded proposal opening, industry experience | templates a freelancer would actually pick |

All tools are annotated `readOnlyHint=true`. Every result carries its type, source, date, outcome (for
proposals) and a `portfolio://` URI, so a host can cite it. The retrieval design (which of the
RAG variants is used) was chosen by measurement; see `evals/results/rag_variants.json`.

## Tests

`pytest mcp_servers/portfolio/tests` (in-memory MCP client) and the schema snapshot in
`tests/contract/test_tool_schemas.py`.
