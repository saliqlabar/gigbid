"""Repository rules CI enforces (brief: "Rules your CI must enforce").

1. No secrets in the repo (key-shaped strings, .env files).
2. Nothing under gigsim/ changed since it was frozen (hash manifest gigsim/FROZEN.sha256).
3. Every prompt lives in agent/prompts/ (no prompt-like string literals in Python under agent/ or rag/).
4. Every number in README.md's results sections appears in a file under evals/results/.
Exit code 1 on any violation.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
errors: list[str] = []


def tracked() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()
    return [ROOT / p for p in out if (ROOT / p).is_file()]


SECRET = re.compile(r"(gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                    r"AKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9-]{20,}|-----BEGIN (RSA |EC )?PRIVATE KEY-----)")


def check_secrets(files):
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if f.name == ".env" or (f.name.startswith(".env.") and f.name != ".env.example"):
            errors.append(f"secret file tracked: {rel}")
        if f.suffix in (".png", ".pdf", ".sqlite3", ".zip") or "snapshots" in rel or rel.endswith(".lock"):
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if (m := SECRET.search(text)):
            errors.append(f"secret-looking string in {rel}: {m.group(0)[:8]}...")


def gigsim_manifest() -> dict[str, str]:
    out = {}
    for f in sorted((ROOT / "gigsim").rglob("*")):
        if f.is_file() and f.name != "FROZEN.sha256" and "__pycache__" not in f.parts and not f.name.endswith(".sqlite3"):
            out[f.relative_to(ROOT).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def check_gigsim(update: bool):
    path = ROOT / "gigsim" / "FROZEN.sha256"
    cur = gigsim_manifest()
    if update or not path.exists():
        path.write_text(json.dumps(cur, indent=1, sort_keys=True))
        return
    frozen = json.loads(path.read_text())
    for k in sorted(set(cur) | set(frozen)):
        if cur.get(k) != frozen.get(k):
            errors.append(f"gigsim/ changed: {k}")


PROMPT_HINT = re.compile(r"(?i)\b(you are (the|a|an) |return only json|=== user ===)")


def check_prompts(files):
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if f.suffix != ".py" or not (rel.startswith("agent/") or rel.startswith("rag/")) or rel.startswith("agent/testing/"):
            continue
        for m in re.finditer(r'("""|\'\'\'|"|\')(.*?)\1', f.read_text(), flags=re.S):
            s = m.group(2)
            if len(s) > 80 and PROMPT_HINT.search(s) and "docstring" not in s:
                line = f.read_text()[:m.start()].count("\n") + 1
                if not f.read_text().splitlines()[line - 1].lstrip().startswith(('"""', "'''")) or line > 3:
                    errors.append(f"prompt-like string literal outside agent/prompts: {rel}:{line}")


NUM = re.compile(r"(?<![\w.])(\d+\.\d+|\d{2,})(?![\w.])")


def check_readme_numbers():
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text()
    blob = " ".join(p.read_text(errors="ignore") for p in (ROOT / "evals" / "results").glob("*") if p.is_file())
    in_results = False
    for line in text.splitlines():
        if line.startswith("#"):
            in_results = bool(re.search(r"(?i)result|benchmark|comparison|budget", line))
            continue
        if in_results and line.startswith("|") and "---" not in line:
            for n in NUM.findall(line):
                if n not in blob and n.rstrip("0").rstrip(".") not in blob:
                    errors.append(f"README number {n} not found in evals/results/: {line.strip()[:80]}")


if __name__ == "__main__":
    files = tracked()
    check_secrets(files)
    check_gigsim(update="--freeze-gigsim" in sys.argv)
    check_prompts(files)
    check_readme_numbers()
    for e in errors:
        print("ERROR:", e)
    print("repo checks:", "FAILED" if errors else "ok")
    sys.exit(1 if errors else 0)
