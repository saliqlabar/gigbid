"""Render docs/report/report.md to docs/report/GigBid_Report.pdf.

    python scripts/docs/build_report.py

Dependency-free Markdown subset (headings, paragraphs, tables, fenced code, lists, bold/italic/code, sub) to HTML,
then Playwright's Chromium (already installed for the e2e tests, apps/web/node_modules) prints it to A4 PDF.
Run scripts/docs/fill_results.py first so the tables are current.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "report" / "report.md"
HTML_OUT = ROOT / "docs" / "report" / "report.html"
PDF_OUT = ROOT / "docs" / "report" / "GigBid_Report.pdf"

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: 'DejaVu Serif', Georgia, serif; font-size: 10pt; line-height: 1.38; color: #111; }
h1 { font-size: 17pt; margin: 0 0 4pt; } h2 { font-size: 12.5pt; margin: 13pt 0 4pt; border-bottom: 1px solid #999; }
h3 { font-size: 11pt; margin: 9pt 0 3pt; } p { margin: 4pt 0; text-align: justify; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.3pt; background: #f2f2f2; padding: 0 2px; }
pre { font-size: 7.6pt; background: #f5f5f5; padding: 5pt; border: 1px solid #ddd; white-space: pre; overflow: hidden; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 5pt 0; font-size: 8pt; width: 100%; page-break-inside: avoid; }
th, td { border: 1px solid #aaa; padding: 2pt 4pt; text-align: left; vertical-align: top; }
th { background: #e8e8e8; } sub { font-size: 7.5pt; color: #444; } ul { margin: 3pt 0 3pt 16pt; padding: 0; }
li { margin: 1pt 0; } em { color: #333; }
"""


def inline(s: str) -> str:
    parts = re.split(r"(`[^`]+`)", s)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{html.escape(p[1:-1])}</code>")
            continue
        keep = re.findall(r"</?sub>", p)
        p = html.escape(p)
        if keep:
            p = p.replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        p = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"<em>\1</em>", p)
        out.append(p)
    return "".join(out)


def convert(md: str) -> str:
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S)
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    lines, out, i = md.splitlines(), [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif m := re.match(r"(#{1,3}) (.*)", ln):
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows if not re.match(r"^\|[\s|:-]+\|?$", r)]
            t = "<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
            out.append(t + "</table>")
        elif re.match(r"\s*[*-] ", ln):
            items = []
            while i < len(lines) and (re.match(r"\s*[*-] ", lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                if re.match(r"\s*[*-] ", lines[i]):
                    items.append(re.sub(r"\s*[*-] ", "", lines[i], count=1))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
        elif not ln.strip():
            i += 1
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,3} |```|\||\s*[*-] )", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def main() -> None:
    body = convert(SRC.read_text())
    HTML_OUT.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>GigBid report</title>"
                        f"<style>{CSS}</style></head><body>{body}</body></html>")
    js = (f"const {{chromium}} = require('playwright');(async()=>{{const b=await chromium.launch();const p=await b.newPage();"
          f"await p.goto('file://{HTML_OUT}');await p.pdf({{path:'{PDF_OUT}',format:'A4',printBackground:true,"
          f"displayHeaderFooter:true,headerTemplate:'<span></span>',footerTemplate:'<div style=\"font-size:7pt;width:100%;"
          f"text-align:center\">GigBid R&amp;D report · page <span class=\"pageNumber\"></span> of <span class=\"totalPages\"></span></div>',"
          f"margin:{{top:'16mm',bottom:'16mm',left:'15mm',right:'15mm'}}}});await b.close();}})();")
    env = dict(os.environ, NODE_PATH=str(ROOT / "apps" / "web" / "node_modules"))
    node = os.environ.get("NODE", str(Path.home() / ".local" / "node" / "bin" / "node"))
    subprocess.run([node if Path(node).exists() else "node", "-e", js], check=True, env=env, cwd=ROOT / "apps" / "web")
    print("wrote", PDF_OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
