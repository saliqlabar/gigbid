# 0001 One marketplace port, two adapters

**Status:** accepted · **Context:** R1, A4, viva question 7.

**Decision.** All marketplace access goes through `agent/marketplace_port.py` (a `Protocol`), wrapped by the
Marketplace MCP server. Two adapters implement it: `adapters/gigsim.py` (REST) and `adapters/upwork_live.py`
(the official Upwork MCP server through `MultiServerMCPClient` over Streamable HTTP, OAuth 2.1). The adapter is
picked by `marketplace.mode` in `agent/config.yaml` (env `MARKETPLACE_MODE` overrides).

**Consequences.** Graph code, prompts and MCP tool schemas do not change between GigSim and live mode; only
the adapter and the tool-name map (`marketplace.upwork_tools`) do. In live mode `submit_proposal` and
`send_message` return `awaiting_user_confirmation`: Upwork requires the user to confirm the draft there.
Live-mode tool names must be checked against Upwork's `tools/list` once connected; an unmapped method fails
loudly ("not supported in live mode") instead of guessing.
