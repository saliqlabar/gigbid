---
title: "GigBid — R&D design review"
subtitle: "AI-4016 Assignment II · Muhammad Saliq Manzoor Labar (23i-0611)"
---

# GigBid — R&D design review

AI-4016 Assignment II · Muhammad Saliq Manzoor Labar · repository `github.com/saliqlabar/gigbid`

Every number in a table below is generated from a file in `evals/results/` by `scripts/docs/fill_results.py`,
and CI (`scripts/ci/check_repo.py`) fails if a number in this report's tables is not found there. Commits are
cited by short hash. Local runs used one RTX 4060 Laptop GPU (8 GB) under WSL2 with Ollama, so open-model
cost is 0 USD; hosted numbers (Groq) are marked as such.

## 1. Problem and constraints

**The workflow.** A freelancer on Upwork spends most of their bidding time on four things: reading job posts
to decide whether a job is worth Connects at all, spotting scams (off-platform payment, unpaid "test tasks",
brand-new unverified clients), pricing, and writing a proposal that points at real past work instead of a
generic pitch. Then client replies have to be answered quickly. GigBid automates the reading, judging,
pricing and drafting, and keeps the human in charge of everything that leaves the system.

**What must never be automated** (enforced in code, section 9): submitting a proposal, sending a message and
spending Connects. Each is behind a LangGraph `interrupt()`; the per-user autonomy setting may auto-*skip*
low-fit jobs but there is no setting that auto-sends. GigBid also never scrapes or drives a website: in live
mode it talks only to Upwork's official MCP server, whose writes are previews the user confirms on Upwork.

**Budgets** (declared in `agent/config.yaml → budgets.declared` on 2026-11-14, before the final eval runs;
section 10 compares them with what was measured):

| Environment | p95 s per job analysed | p95 s per proposal | USD per job analysed | USD per proposal |
|---|---|---|---|---|
| local (open profile, RTX 4060) | 45 | 420 | 0.0 | 0.0 |
| hosted (open_hosted profile, Groq) | 10 | 60 | 0.002 | 0.01 |

Per-thread limits: 30 graph steps, 40000 tokens, at most 2 Critic revisions; exceeding a limit moves the thread
to a visible `needs_attention` state.

**Data constraint.** The starter kit we received did not contain GigSim's seed data, the persona corpora or the
dev set. Following the instructor's note, these were built from public Kaggle Upwork datasets plus clearly
labelled synthetic data (`docs/DATA_CARD.md`): 3 synthetic personas, a 200-job dev set per persona
(130 in-persona, 45 off-persona, 25 scams; some in-persona jobs carry planted injections). **Gold labels are
rule-derived, not human-annotated**, which bounds every accuracy number below: GigBid is partly being scored on
how well it rediscovers my labelling rules.

## 2. Architecture

```
React (Vite, TS) --SSE/REST--> FastAPI --enqueue--> arq worker (Redis)
                                  |                       |
                                  +--- LangGraph bid + conversation graphs (Postgres checkpointer + store)
                                                          |  per-node MCP tool allowlists
            +---------------------+-----------------------+-----------------------+
       Portfolio MCP         Marketplace MCP          Workspace MCP          GitHub MCP (official)
       (RAG, read-only)      one port: GigSim |       (calendar holds,       (repo ingestion)
                             Upwork MCP (OAuth)        email drafts)
```

**Bid graph** (one thread per job, `agent/graphs/bid_graph.py`, rendered from code in `docs/ARCHITECTURE.md`):
`intake` fans out with `Send` to Job Analyst, Risk Assessor and Fit Matcher in parallel; they join at a
deterministic `decide` node (skip / watch / bid, thresholds in config); then Pricing Strategist → Proposal
Writer → claim verification → Critic (≤ 2 send-backs) → Compliance Guard → `approval` (`interrupt()`) →
`submitter`. **Conversation graph** (one thread per client conversation): read messages → classify intent
(question, negotiation, interview request, scam signal) → draft a reply or offer interview slots from the
calendar → approval → send.

**Why each agent exists.** Each node exists because a later step *decides* on its output, so it must be a typed
object rather than prose (`agent/handoffs.py`: `JobSpec`, `RiskReport`, `FitReport`, `BidDecision`,
`PriceQuote`, `ProposalDraft`, `Critique`, `ComplianceVerdict`). The Risk Assessor is separate so it can veto a
bid on its own; the Fit Matcher is the retrieval step that question B measures; `decide` and the Compliance
Guard are rules, not LLM calls, so a skip or a block can always be explained; only the Writer and Critic use the
strong model. Whether this split is worth its cost is question C.

**The marketplace port.** Agents never call a marketplace. They call the Marketplace MCP server, which talks to
one port (`agent/marketplace_port.py`) with two adapters: GigSim (development, grading) and live Upwork (the
official Upwork MCP server over OAuth 2.1). The adapter is chosen by `marketplace.mode` in config; no agent
code changes between them (contract tests in `tests/contract/`, mapping tests in
`tests/unit/test_upwork_live_mapping.py`).

**MCP surface.** Three servers written for GigBid (Portfolio, Marketplace, Workspace) plus the official GitHub
MCP server; every tool has a JSON schema, MCP annotations (read-only / destructive / idempotent), an
idempotency key on every write, and service-token identity in HTTP mode (`docs/TOOLS.md`). Each node has an
allowlist of tools (`agent/mcp_client.py`), so for example the Writer cannot call `submit_proposal`.

**Stores, kept apart.** *State* (`agent/state.py`): typed `BidState` / `ConversationState`, checkpointed in
Postgres. *Execution trace*: every tool call, argument, result and LLM call in the run-event log and Arize
Phoenix, PII masked. *Long-term memory*: a LangGraph store with two namespaces, style memory (from the user's
edits) and outcome memory (per job category); only `agent/memory/promotion_rules.py` writes to it, and only when
a signal repeats (style: in ≥ 3 of the last 5 edits; outcome: ≥ 3 outcomes in a category). Raw tool output never
enters memory.

**Durability.** Approvals resume the checkpointed thread; submission uses idempotency keys, so killing the API
between approval and submission still submits exactly once
(`tests/integration/test_bid_graph.py::test_crash_and_resume_submits_exactly_once`).

## 3. R&D question A — tool granularity

**Hypothesis.** A middle-grained tool surface (GigBid's real MCP tools) keeps most of the selection accuracy of
many fine-grained tools at a lower token cost, and a three-tool coarse surface (`action` + `params`) loses
argument accuracy because the model has to guess free-form parameter names.

**Setup** (`evals/runners/tool_granularity.py`). 40 natural-language tasks with a known correct call, one
tool-calling turn, the cheap model (qwen3:4b-instruct, local). Three surfaces: *fine* (one tool per narrow
action), *coarse* (three tools), *best* (the schemas exported from the three GigBid servers). Metrics: right tool
(and right `action` for coarse), every expected argument present with the expected value, and tokens per task
including the tool definitions themselves. After the first run the *best* surface was re-run with rewritten tool
descriptions (commit `65c263d`).

<!-- RESULTS:tools -->
| Tool surface | Tools | Selection accuracy | Argument accuracy | Tokens / task |
|---|---|---|---|---|
| fine | 33 | 0.975 | 0.975 | 2205 |
| coarse | 3 | 0.750 | 0.450 | 519 |
| best (descriptions as shipped) | 17 | 0.925 | 0.875 | 2095 |
| best, rewritten descriptions | 17 | 1.000 | 0.975 | 2405 |

<sub>Source: `tool_granularity_cheap_shipped.json`, `tool_granularity_cheap_rewritten.json` (40 tasks, qwen3:4b-instruct)</sub>
<!-- /RESULTS:tools -->

**Conclusion.** Coarse tools are cheapest per task but lose almost half of the argument accuracy: the model has
to put the right keys inside an untyped `params` object, and it often does not. The fine surface is the most
accurate but costs the most tokens, because 33 tool definitions are sent on every call. GigBid's surface, as
first shipped, sat between them; **rewriting the descriptions (no schema change) moved it from below fine to
level with or above fine** on both accuracies at roughly the same token cost. The lesson for the design: tool
*descriptions* mattered more than tool *count* in this range, so the descriptions as rewritten are what ships.
Limit: 40 tasks, one model, single-turn calls; multi-step tool use was not measured here.

## 4. R&D question B — advanced RAG

**Variants** (`rag/variants/`, one flag per component, so ablation removes one flag at a time):
V0 naive (fixed chunks, one dense query); V1 requirement decomposition (one query per `JobSpec` requirement);
V2 hybrid (BM25 + dense, reciprocal rank fusion); V3 cross-encoder rerank (jina-reranker-v1-turbo-en);
V4 outcome boost (evidence that won past bids ranks higher); V5 multi-vector (each project indexed as case
study, README and code separately, then merged per project); V8 corrective retrieval (an LLM grades the
retrieved set and rewrites the query when it is weak). Embeddings: nomic-embed-text-v1.5 (fastembed).

**Hypotheses.** I did not write a hypothesis down before the retrieval runs, so the retrieval comparison is
reported as exploratory. Written down on 2026-11-14 (this commit), **before** the proposal/outcome runs:
(H-B1) the best retrieval variant raises GigSim reply rate over V0 by at least 10 points on the fixed set;
(H-B2) it lowers unsupported claims per proposal (before verification) relative to V0. After seeing the
retrieval numbers I also check the brief's example claim: decomposition plus hybrid retrieval raises Recall@5 by
15 points over V0 on jobs with four or more requirements.

**Claim verification** (`agent/nodes/verifier.py`, all variants). The draft is split into claims; each is checked
against the evidence the Writer cited and labelled supported / partly supported / unsupported; unsupported
sentences are removed or rewritten before the Critic sees the draft. Unsupported claims are counted before
verification (what the verifier removed) and after (an independent second verification pass on the final text).

**Results** (all bid jobs with gold evidence, macro-averaged over personas; proposal columns on a fixed set of
5 gold-bid jobs per persona):

<!-- RESULTS:rag -->
| Variant | Evidence Recall@5 | MRR | nDCG@10 | Unsupported claims / proposal | Judge score (1-5) | GigSim reply rate | p95 latency (s) | Cost / proposal (USD) |
|---|---|---|---|---|---|---|---|---|
| v0_naive | 0.242 | 0.405 | 0.222 | n/a | n/a | n/a | n/a | n/a |
| v1_decompose | 0.268 | 0.492 | 0.255 | n/a | n/a | n/a | n/a | n/a |
| v2_hybrid | 0.284 | 0.467 | 0.260 | n/a | n/a | n/a | n/a | n/a |
| v3_rerank | 0.277 | 0.483 | 0.264 | n/a | n/a | n/a | n/a | n/a |
| v4_outcome | 0.276 | 0.457 | 0.259 | n/a | n/a | n/a | n/a | n/a |
| v5_multivector | 0.327 | 0.395 | 0.316 | n/a | n/a | n/a | n/a | n/a |

<sub>Source: `evals/results/rag_variants.json` (all bid jobs with gold evidence, 3 personas); proposal columns from `outcomes_v0.json` / `outcomes_best.json` (fixed set, 5 jobs per persona, local open models, 0 USD)</sub>
<!-- /RESULTS:rag -->

Recall@5 by persona and by job type (1–3 vs 4+ requirements) for every variant is in Appendix C.

Ablation of the best retrieval variant (one component removed at a time):

<!-- RESULTS:ablation -->
*Pending: `evals/results/rag_ablation.json` has not been produced yet.*
<!-- /RESULTS:ablation -->

Judge calibration (I labelled 30 proposals blind, 1–5, and compared with the LLM judge):

<!-- RESULTS:judge -->
*Pending: `evals/results/judge_calibration.json` has not been produced yet.*
<!-- /RESULTS:judge -->

The live deployment cannot hold nomic-embed in 512 MB, so it uses bge-small; the cost of that, measured:

<!-- RESULTS:deploy_embedding -->
*Pending: `evals/results/rag_deploy_embedding.json` has not been produced yet.*
<!-- /RESULTS:deploy_embedding -->

**Analysis.** _To be completed from the results above (H-B1, H-B2, where retrieval gains did not reach the reply
rate and why)._

**What ships.** _One-paragraph decision, written after the outcome runs._

## 5. R&D question C — single agent vs prebuilt supervisor vs hand-built graph

**Hypothesis** (written 2026-11-14, before the run): the hand-built graph has higher scam recall and fewer
compliance violations left in the final letter than the single ReAct agent, because the risk veto and the
Compliance Guard are deterministic steps the model cannot skip; the ReAct agent uses fewer tokens and may match
the graph on bid/skip accuracy for simple, single-requirement jobs.

**Setup** (`evals/runners/study_c.py`). The same 50 dev jobs (stratified by job type, fixed seed) through
(a) one ReAct agent (`create_react_agent`) holding all read tools, (b) a prebuilt supervisor
(`langgraph-supervisor`) over analyst / risk / fit / writer agents, (c) GigBid's bid graph. Same MCP tools, same
strong model for the baselines, same harness approval. Unsupported claims are counted by one independent
verifier pass on each system's final letter against the evidence that system actually saw; compliance violations
by applying the Compliance Guard's rules after the fact.

<!-- RESULTS:study_c -->
*Pending: `evals/results/study_c.json` has not been produced yet.*
<!-- /RESULTS:study_c -->

**Analysis.** _To be completed from the results._

## 6. The change request

**What changed.** Platform policy 1.1 (`evals/change_request/policy_v1_1.yaml`): (1) a +2 Connects surcharge per
proposal, (2) a 300-word limit on cover letters, (3) a $10/h minimum hourly bid.

**What I had to edit.** The policy is data: GigSim serves it from `GET /policy`, the Pricing Strategist reads
the Connects cost from it, and the Compliance Guard turns its `rules.*` into checks (`data/policy_pack/rules.yaml`
holds only the patterns). So (1) and (2) needed **no code change**: the surcharge flows into the Connects cost and
expected value, and the word limit was already a policy-driven rule. Only (3) was new behaviour: four lines in
`agent/nodes/pricing.py` that raise an hourly bid to the policy's minimum, plus a test
(`tests/integration/test_change_request.py`). Commit `7e5dd68`; started 04:55, committed 05:00 (2026-11-14).

**Effect, replayed** (`evals/change_request/run.py`, deterministic, no LLM calls): GigSim is started on a seed
folder whose `policy.yaml` is version 1.1, every bid the decision eval made is re-priced with the production
`quote()` under both policies, and every drafted letter is re-checked by the production Compliance Guard.

<!-- RESULTS:change_request -->
*Pending: `evals/results/change_request.json` has not been produced yet.*
<!-- /RESULTS:change_request -->

**What it says about the design.** Treating the platform's rules as data fetched at run time, not constants in
prompts or code, meant the change request was a config change plus one new pricing rule. Its weak spot: a rule of
a *new kind* (a price floor) still needs code, because the pricing logic only knew how to read costs, not limits.

## 7. Signature extension — X1 agency mode

**What I built** (`agent/agency.py`, `docs/EXTENSION.md`). A three-person agency (the three personas). With agency
mode on, each new job goes to `route_job`, which runs the production Fit Matcher once per member in parallel,
each through the Portfolio MCP server with that member's freelancer id (so one member's evidence never leaks into
another's proposal), ranks members by fit and number of strong requirement matches, and skips the job if nobody
reaches `agency.min_fit_to_route`. The bid graph then runs as that member, and the Writer drafts in their voice.
The routing decision, with every member's fit and missing skills, is stored in the thread and shown on the Job
detail page.

**How I measured it** (`evals/runners/agency_eval.py`). Ground truth: a job labelled *bid* in persona P's dev set
belongs to member P (scams excluded). Metric: routing accuracy per member, confusion matrix, routing latency.
Baseline: the obvious cheap rule, job category → member.

<!-- RESULTS:agency -->
*Pending: `evals/results/agency_eval.json` has not been produced yet.*
<!-- /RESULTS:agency -->

**Limit.** A job two members could both do well counts as a miss for whichever was not labelled, so accuracy is a
lower bound on usefulness; routing triples the cheap-model cost of the fit step.

## 8. Failure analysis

Failed dev jobs are taken from the committed eval detail files and grouped by fixed rules (no hand labelling,
`evals/runners/failure_analysis.py`): *wrong skip* (gold bid, GigBid skipped or watched), *wrong bid*, *mispriced*
(outside the fair band), *injection* (planted injection not flagged, or attack text in a sent proposal),
*unsupported claim* (left in the final letter by the independent verifier), *compliance miss*, *generic opening*
(judge's reason), *tool error*. *Missed evidence* is reported from the retrieval eval (Recall@5 by persona and job
type, section 4), because the RAG eval only keeps it in aggregate.

**Fix tried.** After the first decision run (`decisions_open_run1.json`, commit `15c0430`), the largest groups
were wrong skips and unflagged injections. Commit `72d8bb0` (1) strips lines that match injection signatures from
the post before the Risk Assessor and Job Analyst see it (the risk flag is still raised), (2) adds role-tag
signatures (`system:`, `assistant:`) to detection, and (3) lowers the bid threshold from fit 0.40 to 0.33, tuned
on the run-1 sample. The table compares the same jobs before and after that commit.

<!-- RESULTS:failures -->
*Pending: `evals/results/failure_analysis.json` has not been produced yet.*
<!-- /RESULTS:failures -->

**Analysis.** _Ten representative failures with the fix tried and whether it worked, from
`failure_analysis.json`._

## 9. Safety and platform respect

**GigBid must never:** submit a proposal, send a message or spend Connects without the user's approval; contact
a client off-platform or put contact details in a proposal; promise guarantees or unpaid work; claim experience
the portfolio does not show; follow instructions written inside a job post or client message; scrape or automate
a website.

**How it is enforced.**

* *Approval is structural, not a prompt.* `submitter` is only reachable from `approval`, which calls
  `interrupt()`; only the approval API resumes it. `submit_proposal` is annotated destructive, carries an
  idempotency key, and is on the allowlist of the `submitter` node only. In live mode the Upwork MCP server only
  creates previews; the user submits on Upwork.
* *Grounding.* Claim verification removes unsupported claims before the Critic; the approval screen shows the
  evidence behind every remaining claim.
* *Compliance Guard* (rules, not an LLM): redacts e-mail, phone and URLs; blocks guarantees, off-platform
  contact and unpaid-work offers; applies the live policy's limits (word count, Connects). A blocked draft shows
  the reason in the UI.
* *Untrusted input.* Job posts and client messages are delimited and labelled as data in every prompt;
  injection signatures are detected (risk flag) and stripped before analysis (`72d8bb0`).
* *Platform respect.* GigSim's rate limits are honoured (`Retry-After`, backoff); Upwork is reached only through
  its official MCP server with OAuth; no scraping; tokens are stored server-side and never committed (CI secret
  scan).

**Measured: prompt-injection attack success rate** on the planted-injection jobs, at three defence levels
(`evals/runners/injection_asr.py`): *none*, *prompt* (delimiting, prompt lines, detection) and *full*
(production, adds the Compliance Guard on outgoing text). An attack succeeds when the text that would be sent,
or the bid, does what the injection asked.

<!-- RESULTS:asr -->
*Pending: `evals/results/injection_asr.json` has not been produced yet.*
<!-- /RESULTS:asr -->

**What still gets through.** _From the injection and failure results: which templates succeed at the full level
and why._

## 10. Results against the declared budgets

The instructor's hidden test run happens after the deadline against the live URL; at the time of writing there is
no live URL (free hosts that fit the stack required a payment card), so this section reports the dev-set runs
against the budgets declared in section 1, without excuses.

<!-- RESULTS:budgets -->
| Environment | Budget | Declared | Measured | Status |
|---|---|---|---|---|
| local | p95 s per job analysed | 45 | n/a | n/a |
| local | p95 s per proposal | 420 | n/a | n/a |
| local | USD per job analysed | 0.0 | n/a | n/a |
| local | USD per proposal | 0.0 | n/a | n/a |
| hosted | p95 s per job analysed | 10 | n/a | n/a |
| hosted | p95 s per proposal | 60 | n/a | n/a |
| hosted | USD per job analysed | 0.002 | n/a | n/a |
| hosted | USD per proposal | 0.01 | n/a | n/a |

<sub>Source: declared in `agent/config.yaml → budgets.declared`; measured from `decisions_open.json`, `outcomes_best.json` (local) and `decisions_hosted.json`, `outcomes_hosted.json` (hosted, n/a if not run)</sub>
<!-- /RESULTS:budgets -->

<!-- RESULTS:decisions -->
*Pending: `evals/results/decisions_open.json` has not been produced yet.*
<!-- /RESULTS:decisions -->

**One optimisation that helped: skipping early.** The deterministic `decide` node runs before the Writer, so a
job that is skipped or watched never pays for drafting, verification, critique and compliance, which are where
most of a proposal's time and tokens go. Measured on the decision run:

<!-- RESULTS:optimisation -->
*Pending: `evals/results/decisions_open.json + outcomes_best.json` has not been produced yet.*
<!-- /RESULTS:optimisation -->

_Discussion of budgets met and missed._

## Appendix A — reproducing every table

```bash
make install
bash scripts/eval_queue.sh            # full GPU queue, resumable; writes evals/results/*.json
python -m evals.runners.rag_eval --ablate v5_multivector
python -m evals.runners.judge_calibration score   # after filling evals/datasets/judge_labels.csv
python scripts/docs/fill_results.py   # regenerates the tables in README.md, docs/*.md and this report
python scripts/docs/build_report.py   # renders this file to docs/report/GigBid_Report.pdf
```

## Appendix B — AI usage

See `docs/AI_USAGE.md`.


## Appendix C — retrieval by persona and job type

<!-- RESULTS:rag_personas -->
| Variant | Persona | Jobs | R@5 | nDCG@10 | R@5, 1-3 requirements | R@5, 4+ requirements |
|---|---|---|---|---|---|---|
| v0_naive | ai_ml | 83 | 0.171 | 0.167 | 0.135 | 0.205 |
| v0_naive | fullstack | 92 | 0.229 | 0.228 | 0.235 | 0.200 |
| v0_naive | data_analyst | 109 | 0.325 | 0.272 | 0.322 | 0.344 |
| v1_decompose | ai_ml | 83 | 0.231 | 0.209 | 0.230 | 0.233 |
| v1_decompose | fullstack | 92 | 0.231 | 0.248 | 0.237 | 0.200 |
| v1_decompose | data_analyst | 109 | 0.343 | 0.307 | 0.327 | 0.422 |
| v2_hybrid | ai_ml | 83 | 0.251 | 0.226 | 0.265 | 0.237 |
| v2_hybrid | fullstack | 92 | 0.253 | 0.239 | 0.255 | 0.240 |
| v2_hybrid | data_analyst | 109 | 0.350 | 0.313 | 0.344 | 0.378 |
| v3_rerank | ai_ml | 83 | 0.270 | 0.241 | 0.280 | 0.261 |
| v3_rerank | fullstack | 92 | 0.231 | 0.249 | 0.235 | 0.213 |
| v3_rerank | data_analyst | 109 | 0.330 | 0.303 | 0.333 | 0.311 |
| v4_outcome | ai_ml | 83 | 0.236 | 0.209 | 0.270 | 0.205 |
| v4_outcome | fullstack | 92 | 0.238 | 0.236 | 0.235 | 0.253 |
| v4_outcome | data_analyst | 109 | 0.353 | 0.332 | 0.353 | 0.356 |
| v5_multivector | ai_ml | 83 | 0.292 | 0.291 | 0.340 | 0.246 |
| v5_multivector | fullstack | 92 | 0.315 | 0.314 | 0.309 | 0.347 |
| v5_multivector | data_analyst | 109 | 0.374 | 0.344 | 0.375 | 0.367 |

<sub>Source: `evals/results/rag_variants.json` (`personas.*.recall@5_by_job_type`)</sub>
<!-- /RESULTS:rag_personas -->
