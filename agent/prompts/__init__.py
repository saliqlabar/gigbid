"""Versioned prompt files: one .md per node. CI checks no prompt text lives in Python code.

File format:
    ---
    version: 3
    ---
    <system prompt>
    === user ===
    <user template, $variables substituted with string.Template>
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from string import Template

import yaml

DIR = Path(__file__).parent


@lru_cache(maxsize=64)
def load(name: str) -> tuple[dict, str, str]:
    raw = (DIR / f"{name}.md").read_text()
    meta: dict = {}
    if raw.startswith("---"):
        _, fm, raw = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    system, _, user = raw.partition("=== user ===")
    return meta, system.strip(), user.strip()


def render(name: str, variables: dict) -> tuple[str, str, str]:
    """(system, user, version) for a prompt file."""
    meta, system, user = load(name)
    # <defence>...</defence> marks injection-defence instructions. They are always on in production; the
    # injection ASR study (evals/runners/injection_asr.py) turns them off with GIGBID_DEFENCES=none.
    keep = os.getenv("GIGBID_DEFENCES", "full") != "none"
    system, user = (re.sub(r"<defence>(.*?)</defence>", r"\1" if keep else "", x, flags=re.S) for x in (system, user))
    v = {k: (x if isinstance(x, str) else str(x)) for k, x in variables.items()}
    return Template(system).safe_substitute(v), Template(user).safe_substitute(v), str(meta.get("version", "1"))
