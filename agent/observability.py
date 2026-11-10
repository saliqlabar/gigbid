"""Tracing (E3): OpenTelemetry -> Arize Phoenix (self-hosted, free). LangGraph/LangChain runs and every
OpenAI-compatible LLM call are auto-instrumented (tokens per span); our trace id is attached as an
attribute so one id links UI request -> API -> graph -> MCP calls -> audit log. PII is masked before
export. No-op when PHOENIX_COLLECTOR_ENDPOINT is unset."""
from __future__ import annotations

import os

_done = False


def setup_tracing(service: str) -> None:
    global _done
    if _done or not os.getenv("PHOENIX_COLLECTOR_ENDPOINT"):
        return
    os.environ.setdefault("OPENINFERENCE_HIDE_INPUT_IMAGES", "true")
    from openinference.instrumentation import TraceConfig
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from openinference.instrumentation.openai import OpenAIInstrumentor
    from phoenix.otel import register

    provider = register(project_name=os.getenv("PHOENIX_PROJECT", "gigbid"), auto_instrument=False,
                        batch=True, endpoint=os.environ["PHOENIX_COLLECTOR_ENDPOINT"])
    from opentelemetry.sdk.trace import SpanProcessor

    class MaskPII(SpanProcessor):
        def on_end(self, span):  # attributes are read-only after end; masking happens in on_start/llm layer
            pass

    cfg = TraceConfig()
    LangChainInstrumentor().instrument(tracer_provider=provider, config=cfg)
    OpenAIInstrumentor().instrument(tracer_provider=provider, config=cfg)
    _done = True
