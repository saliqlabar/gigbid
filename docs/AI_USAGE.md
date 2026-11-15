# AI usage disclosure

## Tool

**Claude Code** (Anthropic, model Claude Opus 5.5), used as a pair-programmer in the terminal across the
whole project, 11 to 15 November 2026. I directed the work, chose the design (marketplace port, graph shape,
RAG variants to build, signature extension, hosting), reviewed the diffs, ran and checked every evaluation,
and made every commit. Every number in the report comes from a results file the code wrote, never from the
assistant's text.

## What it was used for

| Area | Files | How |
|---|---|---|
| MCP servers and adapters | `mcp_servers/**`, `agent/marketplace_port.py` | drafted servers, schemas, idempotency and audit log; I reviewed the annotations and wrote the tool-design study tasks with it |
| Agents and graphs | `agent/**` | drafted nodes, state, reducers, prompts (each prompt reviewed and edited by me) |
| RAG | `rag/**` | implemented the variants and claim verification from my variant list |
| Web and API | `apps/**` | generated most of the React screens, FastAPI routers and Playwright scripts (tests and the demo recording); I checked them in the browser and with Playwright |
| Evaluation | `evals/runners/**`, `scripts/**` | wrote the runners; I chose sample sizes and read the per-job detail files behind every aggregate |
| Docs | `docs/**`, `README.md` | first drafts from the code and results files; I edited them |
| Synthetic data | `scripts/synth/**`, `scripts/data/**` | generated persona corpora and the dev set from the Kaggle Upwork data (see DATA_CARD.md) |

Judge-calibration labels (`evals/datasets/judge_labels.csv`) are mine alone; the assistant never saw or
proposed scores for them.

## Things it got wrong that were caught

1. **Guessed the Upwork tool names.** The first live adapter mapped port methods to tool names like
   `search_jobs` / `get_job` that do not exist. When the account was connected, Upwork's real `tools/list`
   showed coarse, action-based tools (`upwork__find_jobs` with `action=search|get`) and a preview-then-confirm
   write model. The adapter was rewritten against the recorded list (`docs/upwork_tools_list.json`).
2. **An OAuth flow that could never finish.** The first `/auth/upwork/start` raised an exception inside the
   SDK's redirect handler, which throws away the PKCE verifier and state, so the callback could not complete;
   it also passed an empty tool map, so it failed before OAuth even started.
3. **A fabricated claim slipped through verification.** In a real trace (job_00411) the small verifier model
   labelled "I've also managed IP rotation ... in distributed crawling projects" as *partly supported* with no
   evidence at all, so it was kept. Fixed with a deterministic rule (no evidence ⇒ unsupported) and a unit test
   (`tests/unit/test_verify_rules.py`).
4. **Evaluation that lost its own results.** The RAG evaluation only wrote its results file at the very end, so
   one Ollama crash on the last variant discarded two hours of finished variants. It now saves after each variant.
5. **Running evaluations in parallel on 8 GB.** Running two GPU evaluations at once got Ollama OOM-killed
   (systemd log), which showed up as "connection error" rows in the first decision run; those runs were
   discarded and everything now runs through one sequential queue (`scripts/eval_queue.sh`).
