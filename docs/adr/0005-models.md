# 0005 Model profiles and routing by node

**Status:** accepted · **Context:** "Models: open and local first".

**Decision.** `agent/config.yaml` has profiles `open` (local Ollama on an RTX 4060 8 GB: `qwen3:4b-instruct`
cheap, `qwen3:8b` strong with thinking off, `llama3.2` fallback), `open_alt` (second open configuration for
comparison), `open_hosted` (same open-weight families through Groq for the deployed link, since free hosts have
no GPU) and `test` (scripted fake model for CI). Every node maps to a tier in config, never in code; cheap,
frequent nodes use the small model, Writer / Critic / Liaison / judge the strong one.

**Consequences.** Embeddings (`nomic-embed-text-v1.5`) and reranker run on CPU via fastembed in every profile.
The open profile costs 0 USD; we report tokens per second and hardware instead. The comparison of the two open
configurations is in `evals/results/decisions_open.json` vs `decisions_open_alt.json`.
