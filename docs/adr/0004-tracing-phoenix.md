# 0004 Self-hosted Arize Phoenix for tracing

**Status:** accepted · **Context:** E3, R8.

**Decision.** OpenTelemetry with OpenInference instrumentors (LangChain + OpenAI-compatible clients) exporting
to a self-hosted Phoenix container. It is free, open source, runs in `docker compose`, and records tokens per
LLM span. Our own trace id (`X-Trace-Id`) is attached to every span, MCP call and audit-log row.

**Consequences.** PII masking (`agent/safety/pii.py`) runs before export. Without `PHOENIX_COLLECTOR_ENDPOINT`
tracing is a no-op and the run-event log (`agent/trace.py`) still records every node and tool call.
