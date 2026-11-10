"""LLM access for every node. The model for each node comes from agent/config.yaml (never from code).

- Profiles (`open`, `open_hosted`, `paid`) map tiers (cheap / strong / fallback) to provider + model;
  `models.nodes` maps each node to a tier. All providers are OpenAI-compatible (Ollama, Groq, ...).
- Every call is recorded into the current usage scope: node, model, tokens, latency, USD. Graph nodes
  sum these per thread (token budget, R8 cost/latency reporting).
- Bounded retries; nodes listed in `fallback_nodes` switch to the fallback model when the primary
  keeps failing (C7). Other nodes raise LLMUnavailable and the graph moves the job to dead-letter.
- `structured()` asks for JSON and validates it into a Pydantic hand-off object, with one repair try.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
from contextlib import contextmanager
from typing import Any, TypeVar

from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, ValidationError

from agent import prompts
from agent.settings import load_config

T = TypeVar("T", bound=BaseModel)
_usage: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("gigbid_usage", default=None)
_clients: dict[tuple[str, bool], Any] = {}


class LLMUnavailable(Exception):
    pass


@contextmanager
def usage_scope():
    """Collect usage records for everything called inside the block."""
    records: list[dict] = []
    token = _usage.set(records)
    try:
        yield records
    finally:
        _usage.reset(token)


def _mcfg() -> dict:
    return load_config()["models"]


def profile_name() -> str:
    return os.getenv("MODEL_PROFILE") or _mcfg().get("profile", "open")


def model_for(node: str, tier: str | None = None) -> dict:
    m = _mcfg()
    prof = m["profiles"][profile_name()]
    tier = tier or m["nodes"].get(node, "cheap")
    return dict(prof[tier], tier=tier)


def _client(provider: str, is_async: bool):
    key = (provider, is_async)
    if key not in _clients:
        p = _mcfg()["providers"][provider]
        base = os.getenv(f"{provider.upper()}_BASE_URL", p["base_url"])
        api_key = os.getenv(p.get("api_key_env", ""), "") or "not-needed"
        cls = AsyncOpenAI if is_async else OpenAI
        _clients[key] = cls(base_url=base, api_key=api_key, timeout=float(_mcfg().get("timeout_s", 120)),
                            max_retries=0)
    return _clients[key]


def _params(spec: dict, json_mode: bool, temperature: float) -> dict:
    kw: dict[str, Any] = {"model": spec["model"], "temperature": temperature}
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    if spec.get("reasoning_effort"):
        kw["reasoning_effort"] = spec["reasoning_effort"]
    if spec.get("think") is False and spec["provider"] == "ollama":
        kw["extra_body"] = {"think": False}
    return kw


def _record(node: str, spec: dict, resp, secs: float, prompt_version: str) -> dict:
    u = getattr(resp, "usage", None)
    tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
    price = _mcfg().get("prices", {}).get(f"{spec['provider']}:{spec['model']}", [0, 0])
    rec = {"node": node, "provider": spec["provider"], "model": spec["model"], "tier": spec["tier"],
           "prompt_version": prompt_version, "tokens_in": tin, "tokens_out": tout, "latency_s": round(secs, 3),
           "cost_usd": round((tin * price[0] + tout * price[1]) / 1e6, 6),
           "tokens_per_s": round(tout / secs, 1) if secs > 0 else None}
    lst = _usage.get()
    if lst is not None:
        lst.append(rec)
    return rec


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _text(resp) -> str:
    return _THINK.sub("", resp.choices[0].message.content or "").strip()


def _specs(node: str) -> list[dict]:
    specs = [model_for(node)]
    if node in _mcfg().get("fallback_nodes", []):
        specs.append(model_for(node, "fallback"))
    return specs


class _FakeResp:
    def __init__(self, text: str, tin: int, tout: int):
        from types import SimpleNamespace as NS
        self.choices = [NS(message=NS(content=text))]
        self.usage = NS(prompt_tokens=tin, completion_tokens=tout)


def _fake(node: str, spec: dict, prompt: str, system: str, user: str, ver: str) -> str:
    from agent.testing.fake_llm import respond
    text = respond(prompt, system, user)
    _record(node, spec, _FakeResp(text, len((system + user).split()), len(text.split())), 0.001, ver)
    return text


async def acomplete(node: str, prompt: str, variables: dict, json_mode: bool = False,
                    temperature: float = 0.2, extra_messages: list[dict] | None = None) -> str:
    system, user, ver = prompts.render(prompt, variables)
    if model_for(node)["provider"] == "fake":
        return _fake(node, model_for(node), prompt, system, user, ver)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}] + (extra_messages or [])
    last = None
    for spec in _specs(node):
        for _ in range(int(_mcfg().get("max_retries", 1)) + 1):
            t = time.perf_counter()
            try:
                resp = await _client(spec["provider"], True).chat.completions.create(
                    messages=msgs, **_params(spec, json_mode, temperature))
            except Exception as e:  # noqa: BLE001 - provider errors are retried, then fall back
                last = e
                continue
            _record(node, spec, resp, time.perf_counter() - t, ver)
            return _text(resp)
    raise LLMUnavailable(f"{node}: all models failed ({last})")


def complete_text(node: str, variables: dict, prompt: str | None = None) -> str:
    """Synchronous variant for small helper calls (e.g. the V8 query rewrite)."""
    system, user, ver = prompts.render(prompt or node, variables)
    if model_for(node)["provider"] == "fake":
        return _fake(node, model_for(node), prompt or node, system, user, ver)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last = None
    for spec in _specs(node):
        try:
            t = time.perf_counter()
            resp = _client(spec["provider"], False).chat.completions.create(messages=msgs, **_params(spec, False, 0.2))
            _record(node, spec, resp, time.perf_counter() - t, ver)
            return _text(resp)
        except Exception as e:  # noqa: BLE001
            last = e
    raise LLMUnavailable(f"{node}: all models failed ({last})")


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i >= 0 and j > i else text


def template(model: type[BaseModel]) -> str:
    """A compact fill-in JSON template for a Pydantic model. Small models copy a raw JSON Schema back
    verbatim; a template of `field: <type - description>` is filled in reliably."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def desc(p: dict) -> Any:
        if "$ref" in p:
            return obj(defs[p["$ref"].split("/")[-1]])
        if "anyOf" in p:
            p = next((x for x in p["anyOf"] if x.get("type") != "null"), p["anyOf"][0]) | \
                {k: v for k, v in p.items() if k != "anyOf"}
            if "$ref" in p:
                return obj(defs[p["$ref"].split("/")[-1]])
        t = p.get("type", "any")
        if t == "array":
            return [desc(p.get("items", {}))]
        if t == "object" and "properties" in p:
            return obj(p)
        bits = [t]
        if "enum" in p:
            bits = ["one of " + "|".join(map(str, p["enum"]))]
        if "minimum" in p or "maximum" in p:
            bits.append(f"{p.get('minimum', '')}..{p.get('maximum', '')}")
        if p.get("description"):
            bits.append(p["description"])
        return "<" + " - ".join(bits) + ">"

    def obj(sch: dict) -> dict:
        return {k: desc(v) for k, v in sch.get("properties", {}).items()}

    return json.dumps(obj(schema), indent=1)


async def structured(node: str, prompt: str, variables: dict, model: type[T], temperature: float = 0.1) -> T:
    """Call the node's model in JSON mode and validate into `model` (one repair attempt)."""
    schema = "Fill in this template (replace every <...> with a value; keep the keys):\n" + template(model)
    variables = {**variables, "schema": schema}
    raw = await acomplete(node, prompt, variables, json_mode=True, temperature=temperature)
    try:
        return model.model_validate_json(_extract_json(raw))
    except ValidationError as e:
        fix = [{"role": "assistant", "content": raw},
               {"role": "user", "content": f"That JSON did not validate: {str(e)[:800]}\n"
                                           f"Return ONLY corrected JSON matching the schema."}]
        raw2 = await acomplete(node, prompt, variables, json_mode=True, temperature=0, extra_messages=fix)
        return model.model_validate_json(_extract_json(raw2))
