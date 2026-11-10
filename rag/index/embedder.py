"""Open embedding + reranker models via fastembed (ONNX, CPU): same models in dev, CI and deploy.

Embeddings are cached on disk by (model, text) hash, so re-running an evaluation only embeds new text.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from agent.settings import ROOT, load_config

CACHE_DIR = Path(os.getenv("GIGBID_MODEL_CACHE", ROOT / "data/processed/models"))
EMB_CACHE = Path(os.getenv("GIGBID_EMB_CACHE", ROOT / "data/processed/emb_cache.sqlite3"))
_lock = threading.Lock()

# nomic-embed-text was trained with task prefixes; omitting them measurably hurts retrieval.
PREFIX = {"nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
          "nomic-ai/nomic-embed-text-v1.5-Q": ("search_query: ", "search_document: ")}


def _cfg() -> dict:
    return load_config().get("rag", {})


@lru_cache(maxsize=2)
def _model(name: str):
    from fastembed import TextEmbedding
    return TextEmbedding(name, cache_dir=str(CACHE_DIR))


@lru_cache(maxsize=2)
def _reranker(name: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(name, cache_dir=str(CACHE_DIR))


def _con() -> sqlite3.Connection:
    EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(EMB_CACHE)
    con.execute("create table if not exists emb(h text primary key, v blob)")
    return con


def embed(texts: list[str], kind: str = "doc", model: str | None = None) -> np.ndarray:
    """L2-normalised embeddings, shape (n, d). kind = 'query' | 'doc'."""
    model = model or _cfg().get("embedding_model", "nomic-ai/nomic-embed-text-v1.5")
    q, d = PREFIX.get(model, ("", ""))
    full = [(q if kind == "query" else d) + t for t in texts]
    keys = [hashlib.sha1((model + "\x00" + t).encode()).hexdigest() for t in full]
    out: dict[str, np.ndarray] = {}
    with _lock:
        con = _con()
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for h, v in con.execute(f"select h, v from emb where h in ({','.join('?' * len(part))})", part):
                out[h] = np.frombuffer(v, dtype=np.float32)
        todo = [(k, t) for k, t in zip(keys, full) if k not in out]
        if todo:
            vecs = list(_model(model).embed([t for _, t in todo], batch_size=32))
            for (k, _), v in zip(todo, vecs):
                v = np.asarray(v, dtype=np.float32)
                v /= np.linalg.norm(v) or 1.0
                out[k] = v
            con.executemany("insert or replace into emb values(?,?)", [(k, out[k].tobytes()) for k, _ in todo])
            con.commit()
        con.close()
    return np.stack([out[k] for k in keys]) if keys else np.zeros((0, 768), dtype=np.float32)


def rerank(query: str, docs: list[str], model: str | None = None) -> list[float]:
    model = model or _cfg().get("reranker_model", "jinaai/jina-reranker-v1-turbo-en")
    if not docs:
        return []
    return [float(s) for s in _reranker(model).rerank(query, docs, batch_size=16)]
