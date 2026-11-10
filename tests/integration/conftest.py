import os

import pytest

os.environ["MODEL_PROFILE"] = "test"
os.environ["MCP_BACKEND"] = "inproc"


@pytest.fixture()
def env(tmp_path, monkeypatch, gigsim_adapter):
    """Isolated state dir + app DB, marketplace server bound to the in-process GigSim."""
    monkeypatch.setenv("GIGBID_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GIGBID_SQLITE", str(tmp_path / "app.sqlite3"))
    import agent.memory.store as ms
    import agent.runner as runner
    import agent.trace as tr
    from mcp_servers.marketplace import server as msrv
    runner._ready = tr._ready = ms._ready = False
    msrv.set_port(gigsim_adapter)
    msrv._limiter = msrv.PerUserRateLimiter(100000)
    msrv._idem = msrv.IdempotencyStore(tmp_path / "idem.sqlite3")
    msrv._audit = msrv.AuditLog(tmp_path / "audit.sqlite3")
    return tmp_path
