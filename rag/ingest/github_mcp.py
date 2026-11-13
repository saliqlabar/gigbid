"""Repo ingestion through the official GitHub MCP server (B1, A5).

GigBid did not write this server: it is GitHub's own remote MCP server, used read-only
(`/x/repos/readonly` = only the repos toolset, no write tools exposed). We call two tools:
`get_file_contents` for the README, the file tree and code files. Output has the same shape as
`loaders.load_local_repos`, so the rest of the pipeline cannot tell where a repo came from.

    GITHUB_PERSONAL_ACCESS_TOKEN=... python -m rag.ingest.github_mcp --persona ai_ml owner/repo [owner/repo ...]

Writes data/processed/github/<persona>.json; `load_github_items` reads it back for the indexer.
The token comes from the environment only and is never logged or written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import PurePosixPath

from agent.settings import ROOT
from rag.ingest.loaders import CODE_EXT, EvidenceItem, _clean

GITHUB_MCP_URL = os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/x/repos/readonly")
OUT = ROOT / "data" / "processed" / "github"
MAX_CODE_FILES = 12
MAX_CODE_CHARS = 20_000


def _text(result) -> str:
    """Concatenate the text / embedded-resource parts of an MCP tool result."""
    parts = []
    for c in result.content:
        if getattr(c, "type", "") == "text":
            parts.append(c.text)
        elif getattr(c, "type", "") == "resource" and hasattr(c.resource, "text"):
            parts.append(c.resource.text)
    return "\n".join(parts)


async def _call(session, tool: str, **args):
    r = await session.call_tool(tool, args)
    if r.isError:
        raise RuntimeError(f"{tool} failed: {_text(r)[:200]}")
    return r


async def fetch_repo(session, owner: str, repo: str) -> tuple[str, list[tuple[str, str]]]:
    """README text and up to MAX_CODE_FILES (path, source) pairs, walking the tree breadth-first."""
    readme = ""
    try:
        readme = _text(await _call(session, "get_file_contents", owner=owner, repo=repo, path="README.md"))
    except RuntimeError:
        pass
    code, queue, seen = [], ["/"], 0
    while queue and len(code) < MAX_CODE_FILES and seen < 40:
        path = queue.pop(0)
        seen += 1
        listing = _text(await _call(session, "get_file_contents", owner=owner, repo=repo, path=path))
        try:
            entries = json.loads(listing)
        except ValueError:
            continue
        for e in entries if isinstance(entries, list) else []:
            p = e.get("path", "")
            if e.get("type") == "dir" and not any(s in p for s in ("node_modules", ".git", "venv", "dist")):
                queue.append(p)
            elif e.get("type") == "file" and PurePosixPath(p).suffix in CODE_EXT and len(code) < MAX_CODE_FILES:
                src = _text(await _call(session, "get_file_contents", owner=owner, repo=repo, path=p))
                code.append((p, src[:MAX_CODE_CHARS // MAX_CODE_FILES]))
    return readme, code


async def ingest(persona: str, repos: list[str]) -> list[EvidenceItem]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise SystemExit("GITHUB_PERSONAL_ACCESS_TOKEN is not set")
    items = []
    async with streamablehttp_client(GITHUB_MCP_URL, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            for full in repos:
                owner, repo = full.split("/", 1)
                readme, code = await fetch_repo(session, owner, repo)
                meta = dict(project=f"gh-{repo}", date=None, client_industry=None,
                            extra={"github": full, "via": "github-mcp"})
                if readme:
                    items.append(EvidenceItem(f"{persona}:readme:gh-{repo}", persona, "readme", _clean(readme),
                                              title=f"README - {full}", source=f"github:{full}/README.md", **meta))
                if code:
                    text = "\n\n".join(f"# file: {p}\n{_clean(s)}" for p, s in code)
                    items.append(EvidenceItem(f"{persona}:code:gh-{repo}", persona, "code", text,
                                              title=f"Code - {full}", source=f"github:{full}", **meta))
    return items


def load_github_items(persona: str) -> list[EvidenceItem]:
    p = OUT / f"{persona}.json"
    return [EvidenceItem(**d) for d in json.loads(p.read_text())] if p.exists() else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("repos", nargs="+", help="owner/repo")
    a = ap.parse_args()
    items = asyncio.run(ingest(a.persona, a.repos))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.persona}.json").write_text(json.dumps([i.to_dict() for i in items], indent=1))
    for i in items:
        print(f"{i.evidence_id:45s} {len(i.text):6d} chars  {i.source}")


if __name__ == "__main__":
    main()
