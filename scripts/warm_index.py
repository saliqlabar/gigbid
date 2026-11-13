"""Embed every persona corpus with the configured embedding model and RAG variant (fills the on-disk embedding
cache). Used at image build time so a running server only embeds queries."""
from rag.retriever import get_corpus, get_retriever
from rag.variants.base import Query

if __name__ == "__main__":
    r = get_retriever()
    for p in ("ai_ml", "fullstack", "data_analyst"):
        hits = r.retrieve(Query(text="python api", persona=p, requirements=["python"]), get_corpus(p), k=3)
        print(p, r.name, len(hits))
