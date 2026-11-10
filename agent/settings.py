"""Loads agent/config.yaml once. Rules, models, budgets and thresholds live there, not in code."""
from __future__ import annotations

import logging as _logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("GIGBID_CONFIG", ROOT / "agent" / "config.yaml"))


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text())


for _n in ("httpx", "mcp", "mcp.server.lowlevel.server"):
    _logging.getLogger(_n).setLevel(_logging.WARNING)
