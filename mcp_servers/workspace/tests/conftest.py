"""Shared fixtures: an isolated GigSim instance (temp DB, no random 5xx) served in-process."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="gigsim-test-")
os.environ.setdefault("GIGSIM_DB", os.path.join(_tmp, "gigsim.sqlite3"))
os.environ.setdefault("GIGSIM_ERROR_RATE", "0")
os.environ.setdefault("GIGSIM_RATE_LIMIT_PER_MIN", "100000")
os.environ.setdefault("GIGSIM_API_TOKENS", "dev-token")
os.environ.setdefault("GIGBID_AUDIT_DB", os.path.join(_tmp, "audit.sqlite3"))
os.environ.setdefault("GIGBID_IDEMPOTENCY_DB", os.path.join(_tmp, "idem.sqlite3"))
os.environ.setdefault("WORKSPACE_DB", os.path.join(_tmp, "workspace.sqlite3"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from gigsim import app as gigsim_app  # noqa: E402


@pytest.fixture()
def gigsim():
    """Fresh GigSim DB per test."""
    gigsim_app.init_db(reset=True)
    return gigsim_app


@pytest.fixture()
def gigsim_adapter(gigsim):
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    return GigSimAdapter("http://gigsim", "dev-token", backoff_base_s=0,
                         transport=httpx.ASGITransport(app=gigsim.app))
