# 0003 Exact cosine search in NumPy as the vector store

**Status:** accepted · **Context:** B1/B2; the stack asks for "one vector store".

**Decision.** Each persona has a few hundred evidence items (a few thousand chunks). We keep embeddings in a
NumPy matrix per persona (`rag/index/vector.py`) and do exact cosine search, with an on-disk embedding cache
keyed by (model, text hash) in `rag/index/embedder.py`.

**Why not pgvector / Qdrant.** At this size exact search takes milliseconds, is perfectly accurate (no ANN
recall loss to confound the RAG comparison) and adds no service to deploy on a free host. Swapping it in later
only touches `rag/index/vector.py`.

**Consequences.** Re-indexing after an upload is incremental: only new text is embedded; the matrix is rebuilt in memory.
