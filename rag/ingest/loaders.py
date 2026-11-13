"""Portfolio loaders (B1): CV (PDF), case studies (Markdown), repo READMEs and code, reviews, proposals.

Each source becomes one EvidenceItem with metadata (persona, type, project, skills, date, client
industry, outcome for proposals). Evidence ids are stable (`<persona>:<type>:<project|n>`), so
retrieval results, claim citations and the dev set's gold labels all refer to the same thing.

Repos come from local folders by default; `rag/ingest/github_mcp.py` loads the same shape through the
official GitHub MCP server when a token and repo list are configured.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.settings import ROOT

PERSONA_DIR = ROOT / "data" / "personas"
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".sql", ".ipynb", ".r", ".R", ".yaml", ".yml", ".dax", ".m"}
_SYNTH = re.compile(r"^\s*(<!--|//|#|--)?\s*SYNTHETIC DATA.*$", re.M)


@dataclass
class EvidenceItem:
    evidence_id: str
    persona: str
    type: str                      # cv | case | readme | code | review | proposal
    text: str
    title: str = ""
    project: str | None = None
    skills: list[str] = field(default_factory=list)
    date: str | None = None
    client_industry: str | None = None
    outcome: str | None = None     # proposals only: won | replied | ignored
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str) -> str:
    return _SYNTH.sub("", text).strip()


def load_cv(pdir: Path, persona: str) -> EvidenceItem:
    pdf = pdir / "cv.pdf"
    if pdf.exists():
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
        src = str(pdf.relative_to(ROOT))
    else:
        text, src = (pdir / "cv.md").read_text(), str((pdir / "cv.md").relative_to(ROOT))
    return EvidenceItem(f"{persona}:cv", persona, "cv", _clean(text), title="CV", source=src)


def load_persona(persona: str, root: Path = PERSONA_DIR, repo_items: list[EvidenceItem] | None = None
                 ) -> list[EvidenceItem]:
    pdir = root / persona
    manifest = {m["project"]: m for m in json.loads((pdir / "manifest.json").read_text())}
    items = [load_cv(pdir, persona)]

    for md in sorted((pdir / "case_studies").glob("*.md")):
        m = manifest.get(md.stem, {})
        text = _clean(md.read_text())
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), md.stem)
        items.append(EvidenceItem(f"{persona}:case:{md.stem}", persona, "case", text, title=title,
                                  project=md.stem, skills=m.get("skills", []), date=m.get("date"),
                                  client_industry=m.get("industry"), source=str(md.relative_to(ROOT))))

    items += repo_items if repo_items is not None else load_local_repos(pdir, persona, manifest)

    for r in json.loads((pdir / "reviews.json").read_text()):
        m = manifest.get(r.get("project"), {})
        items.append(EvidenceItem(r["evidence_id"], persona, "review", r["text"],
                                  title=f"Client review ({r.get('rating')}★) - {m.get('name', r.get('project'))}",
                                  project=r.get("project"), skills=m.get("skills", []), date=r.get("date"),
                                  client_industry=r.get("client_industry"), extra={"rating": r.get("rating")},
                                  source=f"data/personas/{persona}/reviews.json"))

    for p in json.loads((pdir / "proposals.json").read_text()):
        items.append(EvidenceItem(p["evidence_id"], persona, "proposal", p["text"],
                                  title=f"Past proposal ({p.get('job_category')}, {p.get('outcome')})",
                                  project=p.get("project"), skills=p.get("skills", []), date=p.get("date"),
                                  client_industry=p.get("client_industry"), outcome=p.get("outcome"),
                                  extra={"job_category": p.get("job_category")},
                                  source=f"data/personas/{persona}/proposals.json"))
    from rag.ingest.github_mcp import load_github_items   # repos ingested via the GitHub MCP server
    return items + load_github_items(persona) + load_uploads(persona)


UPLOADS = ROOT / "data" / "processed" / "uploads"


def load_uploads(persona: str) -> list[EvidenceItem]:
    """Work the user added in the UI (case studies, CVs, past proposals with outcome)."""
    out = []
    base = UPLOADS / persona
    for kind in ("case", "cv", "proposal"):
        for f in sorted((base / kind).glob("*")) if (base / kind).exists() else []:
            if f.suffix.lower() not in (".md", ".txt", ".pdf"):
                continue
            if f.suffix.lower() == ".pdf":
                from pypdf import PdfReader
                text = "\n".join(p.extract_text() or "" for p in PdfReader(f).pages)
            else:
                text = f.read_text(errors="ignore")
            oc = f.with_name(f.name + ".outcome")
            out.append(EvidenceItem(f"{persona}:{kind}:up-{f.stem[:40]}", persona, kind, _clean(text),
                                    title=f"Uploaded: {f.stem}", project=f"up-{f.stem[:40]}" if kind == "case" else None,
                                    outcome=oc.read_text().strip() if oc.exists() else None,
                                    source=str(f.relative_to(ROOT))))
    return out


def load_local_repos(pdir: Path, persona: str, manifest: dict[str, dict]) -> list[EvidenceItem]:
    out = []
    for repo in sorted((pdir / "repos").iterdir()):
        if not repo.is_dir():
            continue
        m = manifest.get(repo.name, {})
        meta = dict(project=repo.name, skills=m.get("skills", []), date=m.get("date"),
                    client_industry=m.get("industry"))
        readme = repo / "README.md"
        if readme.exists():
            out.append(EvidenceItem(f"{persona}:readme:{repo.name}", persona, "readme", _clean(readme.read_text()),
                                    title=f"README - {m.get('name', repo.name)}",
                                    source=str(readme.relative_to(ROOT)), **meta))
        code = [f for f in sorted(repo.rglob("*")) if f.is_file() and f.suffix in CODE_EXT]
        if code:
            text = "\n\n".join(f"# file: {f.name}\n{_clean(f.read_text(errors='ignore'))}" for f in code)
            out.append(EvidenceItem(f"{persona}:code:{repo.name}", persona, "code", text,
                                    title=f"Code - {m.get('name', repo.name)}",
                                    source=str(repo.relative_to(ROOT)), **meta))
    return out


def personas(root: Path = PERSONA_DIR) -> list[str]:
    return sorted(p.name for p in root.iterdir() if (p / "manifest.json").exists())


def load_all(root: Path = PERSONA_DIR) -> list[EvidenceItem]:
    return [it for p in personas(root) for it in load_persona(p, root)]
