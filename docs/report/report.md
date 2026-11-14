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
| v0_naive | 0.242 | 0.405 | 0.222 | 0.889 | 3.89 | 22.2% | 548.1 | 0.0000 |
| v1_decompose | 0.268 | 0.492 | 0.255 | n/a | n/a | n/a | n/a | n/a |
| v2_hybrid | 0.284 | 0.467 | 0.260 | n/a | n/a | n/a | n/a | n/a |
| v3_rerank | 0.277 | 0.483 | 0.264 | n/a | n/a | n/a | n/a | n/a |
| v4_outcome | 0.276 | 0.457 | 0.259 | n/a | n/a | n/a | n/a | n/a |
| v5_multivector | 0.327 | 0.395 | 0.316 | 0.889 | 3.67 | 22.2% | 359.8 | 0.0000 |
| v8_corrective | 0.271 | 0.342 | 0.274 | n/a | n/a | n/a | n/a | n/a |

<sub>Source: `evals/results/rag_variants.json` (all bid jobs with gold evidence, 3 personas); proposal columns from `outcomes_v0.json` / `outcomes_best.json` (fixed set, 5 jobs per persona, local open models, 0 USD)</sub>
<!-- /RESULTS:rag -->

Recall@5 by persona and by job type (1–3 vs 4+ requirements) for every variant is in Appendix C.

Ablation of the best retrieval variant (one component removed at a time):

<!-- RESULTS:ablation -->
| Configuration | R@5 | MRR | nDCG@10 |
|---|---|---|---|
| v5_multivector | 0.327 | 0.395 | 0.316 |
| v5_multivector - decompose | 0.241 | 0.296 | 0.217 |
| v5_multivector - hybrid | 0.316 | 0.394 | 0.301 |
| v5_multivector - rerank | 0.355 | 0.389 | 0.314 |
| v5_multivector - multivector | 0.277 | 0.483 | 0.264 |

<sub>Source: `evals/results/rag_ablation.json`</sub>
<!-- /RESULTS:ablation -->

* *Decomposition carries the most weight*: removing it drops Recall@5 by 8.6 points (0.327 → 0.241).
* *Multi-vector* is the second: without it Recall@5 falls to 0.277, but MRR **rises** to 0.483, which confirms the
  trade described above (more gold projects in the top five, but less often in first place).
* *Hybrid BM25* adds about 1 point.
* *The reranker does not earn its place*: removing it **raises** Recall@5 to 0.355, leaves nDCG@10 almost unchanged
  (0.314 vs 0.316), and removes about 3 s of CPU per search. The jina-turbo reranker's scores separate relevant
  from irrelevant chunks poorly here, the same weakness that broke V8.

Judge calibration (I labelled 30 proposals blind, 1–5, and compared with the LLM judge):

<!-- RESULTS:judge -->
*Pending: `evals/results/judge_calibration.json` has not been produced yet.*
<!-- /RESULTS:judge -->

The live deployment cannot hold nomic-embed in 512 MB, so it uses bge-small; the cost of that, measured:

<!-- RESULTS:deploy_embedding -->
| Variant | R@5 nomic-embed (eval) | R@5 bge-small (deploy) | nDCG@10 nomic | nDCG@10 bge-small |
|---|---|---|---|---|
| v0_naive | 0.242 | 0.264 | 0.222 | 0.244 |
| v5_multivector | 0.327 | 0.339 | 0.316 | 0.306 |

<sub>Source: `rag_variants.json` vs `rag_deploy_embedding.json`</sub>
<!-- /RESULTS:deploy_embedding -->

The smaller embedding model did **not** cost recall on this set: V5's Recall@5 is 0.339 with bge-small against
0.327 with nomic-embed (nDCG@10 0.306 vs 0.316). So the deployment's model is not a downgrade here, though the
difference in either direction is small.

**Analysis — retrieval (exploratory, no hypothesis written in advance).**

* *Each component, in the order added.* Decomposition (V1) is the clearest single gain: Recall@5 0.242 → 0.268 and
  MRR 0.405 → 0.492, because a post with five requirements gets five queries instead of one blurred one. Hybrid
  BM25 (V2) adds a little Recall@5 (0.284) but loses some MRR. The cross-encoder (V3) and the
  outcome boost (V4) did **not** improve Recall@5 over V2, and V3 raised p95 retrieval latency from about 0.005 s to
  about 4 s on the laptop CPU. Multi-vector indexing (V5) is the only large gain, Recall@5 0.327 and the best
  nDCG@10 (0.316), and it is better for every persona (Appendix C).
* *Why V5 wins on recall but has the lowest MRR (0.395).* Indexing each project as case study, README and code
  separately gives a project three chances to match a requirement, so more of the gold projects appear somewhere
  in the top five. The lower MRR means the first-ranked project is more often not a gold one; my untested guess
  is that the per-project merge lets broad README chunks win first place. For proposals this trade is acceptable: the Writer reads the top five, and missing a project costs more than ranking it third.
* *The brief's example claim does not hold here.* On jobs with four or more requirements (76 jobs), decomposition
  plus hybrid (V2) raises Recall@5 from 0.250 to 0.285 (macro over personas), **3.5 points, not 15**. Even V5 only
  reaches 0.320 (+7 points).
* *V8 corrective retrieval* first scored Recall@5 0.037 (`rag_v8_uncalibrated.json`), which is a bug, not a
  result: its relevance thresholds (0.0 / −3.0) were set for a bge reranker, but the shipped jina-turbo reranker
  scores almost every chunk below 0, so the grader called every set "weak" and rewrote good queries into worse
  ones. I calibrated the threshold on the reranker's scores for gold vs other chunks
  (`rag_v8_calibration.json`): the best cut, −3.6, keeps 99.8% of gold chunks but also 98.2% of the rest, so the
  jina scores barely separate relevant from irrelevant. Rerun with it, V8 reaches Recall@5 0.271, **below V5
  (0.327)**, at about twice the p95 latency (up to 6 s). By design V8 returns no evidence for a requirement that is
  still ungrounded after its rewrite, so it trades recall for not handing the Writer weak evidence; on this
  metric that trade loses. V8 is **not** shipped. The uncalibrated file is kept as this component's failure.
* *Absolute recall is low* (best 0.327). Part of that is the evaluation: gold evidence is rule-derived from the
  synthetic portfolios (section 1), so a project that genuinely shows a skill but was not tagged for it counts as
  a miss. The comparison between variants is fairer than the absolute number.

**Analysis — proposals (H-B1, H-B2).** Both hypotheses written before the outcome runs **fail**.

* *H-B1 (reply rate +10 points)*: V0 and V5 both got 22.2%, 2 replies from 9 submitted proposals each. Of 15
  fixed jobs, 9 reached a proposal under both variants (the rest were stopped at `decide`). With 9 proposals one
  reply is 11 points, so this sample can neither show nor rule out a 10-point gain; the honest reading is "no
  evidence that better retrieval moves the GigSim reply rate", not "it does not".
* *H-B2 (fewer unsupported claims)*: before verification V5 drafts had **more** unsupported claims (2.0 per
  proposal vs 1.0). V5 retrieves more projects per requirement, the Writer cites more of them, and more of those
  citations stretch what the evidence says. After verification both are at 0.889, so the verifier, not the
  retriever, is what holds grounding in place.
* *Judge score*: 3.67 (V5) vs 3.89 (V0) on the 1–5 scale; the judge is not yet calibrated against my labels
  (below), so I do not read anything into a 0.2 difference.
* *Latency*: V5's p95 per proposal is lower (360 s vs 548 s); its p50 is similar (342 s vs 361 s), so the
  difference is in the slow tail of 9 letters, not a faster pipeline; I did not trace which step made V0's
  slowest letters slow.

**What ships.** V5 multi-vector stays as the production retriever: it is the clear winner on every retrieval
metric except MRR, costs about 3 s per search on CPU, and the outcome run shows no downside after verification.
But the report does **not** claim it improves outcomes: the gain that could be measured is in retrieval, and the
step that protects the proposal is claim verification. The ablation below shows the reranker should come out (better recall, 3 s
faster). It is still on in the submitted build only because every proposal and outcome number in this report was
measured with it; turning it off is one flag (`rerank=False` in the V5 preset, `rag/variants/v5_multivector.py`) and is the first change I would
make after submission.

**What ships.** _One-paragraph decision, written after the outcome runs._

## 5. R&D question C — single agent vs prebuilt supervisor vs hand-built graph

**Hypothesis** (written 2026-11-14, before the run): the hand-built graph has higher scam recall and fewer
compliance violations left in the final letter than the single ReAct agent, because the risk veto and the
Compliance Guard are deterministic steps the model cannot skip; the ReAct agent uses fewer tokens and may match
the graph on bid/skip accuracy for simple, single-requirement jobs.

**Setup** (`evals/runners/study_c.py`). The same dev jobs (50 requested, 49 after the per-persona stratified
sample rounds down; fixed seed) through
(a) one ReAct agent (`create_react_agent`) holding all read tools, (b) a prebuilt supervisor
(`langgraph-supervisor`) over analyst / risk / fit / writer agents, (c) GigBid's bid graph. Same MCP tools, same
strong model for the baselines, same harness approval. Unsupported claims are counted by one independent
verifier pass on each system's final letter against the evidence that system actually saw; compliance violations
by applying the Compliance Guard's rules after the fact.

<!-- RESULTS:study_c -->
| System | Jobs | Failed | Bid/skip acc. | Bid F1 | Scam recall | Scams bid on | Unsupported / proposal | Violations left | Violations caught | Reply rate | Tokens / job | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| react | 49 | 0 | 0.653 | 0.514 | 1.000 | 0 | 2.000 | 0 | 0 | 0.0% | 2049 | 21.7 |
| supervisor | 49 | 0 | 0.735 | 0.649 | 1.000 | 0 | 2.571 | 3 | 0 | 7.1% | 1743 | 38.2 |
| graph | 49 | 0 | 0.837 | 0.809 | 1.000 | 0 | 0.737 | 0 | 0 | 17.6% | 8716 | 229.4 |

<sub>Source: `evals/results/study_c.json` (same 50 dev jobs; baselines on qwen3:8b)</sub>
<!-- /RESULTS:study_c -->

**Analysis.** The hypothesis is **partly supported**.

* *Scam recall did not separate the systems*: all three caught every scam (1.000) and none bid on one, so the
  deterministic risk veto bought nothing over a strong model here. The 9 scam jobs in this sample are obvious
  ones; the difference showed up elsewhere.
* *Compliance violations left in the final letter*: graph 0, ReAct 0, supervisor 3. All three of the supervisor's
  were on injection jobs, where its writer agent followed text from the post. ReAct reached 0 a different way: it
  refused to bid on almost every injection job (bid/skip accuracy 0.182 on those 11 jobs, against 0.727 for the
  graph), so it was safe by skipping work the gold labels say was worth bidding on.
* *Grounding is the clearest gain*: 0.737 unsupported claims per proposal for the graph against 2.000 (ReAct) and
  2.571 (supervisor). That is the claim-verification step, which neither baseline has.
* *Decisions*: bid/skip accuracy 0.837 (graph) vs 0.735 (supervisor) vs 0.653 (ReAct), bid F1 0.809 vs 0.649 vs
  0.514. The second half of the hypothesis is **not** supported: ReAct did not match the graph on simple
  in-persona jobs either (0.667 vs 0.750).
* *Cost*: the graph used about 4× the tokens per job (8716 vs 2049) and its p95 latency was 229 s against 22 s,
  because it is the only system that drafts, verifies, critiques and revises. Its median (10.5 s) is lower than
  either baseline's because skipped jobs stop at `decide`.
* *Reply rate* (graph 17.6%, supervisor 7.1%, ReAct 0%) is from 12–19 submitted proposals per system, too few to
  call a difference.

**Caveats.** The graph's analysis nodes use the cheap model and the baselines use qwen3:8b throughout, so the
comparison is "architecture with its intended model mix", not one model held fixed. 49 jobs, one seed.

**Decision.** The hand-built graph ships: it is the only system with no violations left *and* no refusals of good
jobs, and it has a third of the unsupported claims. The price is tokens and tail latency on jobs it bids on.

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
|  | Value |
|---|---|
| GigSim served policy 1.1 unchanged | True |
| Code commit / files | `7e5dd68` · agent/nodes/pricing.py, evals/change_request/policy_v1_1.yaml, tests/integration/test_change_request.py |
| Bids re-priced (from `decisions_open_detail.jsonl`) | 45 |
| Connects spent, policy 1.0 → 1.1 | 202 → 292 |
| Hourly bids raised to the $10/h minimum | 0 of 35 |
| Mean expected value per bid (USD), 1.0 → 1.1 | 283.24 → 282.94 |
| Bids no longer worth the Connects | 0 |
| Drafted letters re-checked | 18 |
| Blocked by Compliance Guard, 1.0 → 1.1 | 0 → 0 |
| Newly blocked by the 300-word limit | 0 |

<sub>Source: `evals/results/change_request.json`</sub>
<!-- /RESULTS:change_request -->

Read plainly: the surcharge is the only rule that bit. All 45 bids still went ahead, but they cost 90 more Connects
(2 each), and the mean expected value per bid fell by only $0.30, so no bid stopped being worth its Connects. The
$10/h floor raised none of the 35 hourly bids, because the personas' rates are well above it; it is tested in
`tests/integration/test_change_request.py` rather than shown by this data. No drafted letter was over 300 words
(the Writer is already asked for 250), so the new limit blocked nothing.

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
| Category | Failures (final runs) | Decision failures before fix | After fix (same jobs) | Fixed |
|---|---|---|---|---|
| wrong skip | 9 | 18 | 9 | 11 |
| unsupported claim | 6 | n/a | n/a | n/a |
| wrong bid | 4 | 2 | 4 | 0 |
| tool error | 3 | 11 | 1 | 10 |

<sub>Source: `evals/results/failure_analysis.json` (22 failures on 21 jobs; fix comparison on 120 jobs common to both decision runs)</sub>
<!-- /RESULTS:failures -->

**Did the fix work?** Run 1 had 11 jobs that failed for infrastructure reasons, not decisions (Ollama was
killed by the WSL memory limit, `LLMUnavailable: all models failed`; fixed by raising the WSL limit, not by code).
To keep those out of the comparison, the 109 jobs that completed cleanly in **both** runs were compared from the
two detail files: bid precision 0.931 → 0.925, bid recall **0.600 → 0.822**, and planted injections flagged
**16/20 → 20/20**. So the fix bought about 22 points of bid recall for under 1 point of precision. The honest
caveat: the new 0.33 threshold was tuned on this same sample, so part of the recall gain is fitting the sample;
a fresh sample would likely show less.

**Representative failures** (job ids from `failure_analysis.json`; 22 failures on 21 jobs in the final runs).

| Job | Category | What happened | Fix / status |
|---|---|---|---|
| ai_ml `job_00512` | wrong skip | fit 0.30, just under the 0.33 bid threshold → watch | threshold edge; left as is (user setting) |
| data_analyst `job_00200` | wrong skip | in-persona job, Fit Matcher scored 0.125 → skip | not fixed; the data_analyst pattern in section 10 |
| fullstack `job_00861` | wrong skip | injection job, risk 0.755 → skipped as scam | arguably right: the post did try to manipulate the bidder |
| data_analyst `job_00882`, `00924`, `00933`, fullstack `00923` | wrong skip | injection jobs watched/skipped on low fit (0.125–0.167) | not fixed, see below |
| fullstack `job_00291`, data_analyst `job_00365` | wrong bid | off-persona jobs at fit exactly 0.333 → bid | **caused by the fix**: the threshold moved to 0.33 |
| ai_ml `job_00694`, fullstack `00529` | wrong bid | off-persona jobs at fit 0.42 and 0.60 | Fit Matcher over-scores partial overlap |
| data_analyst `job_00876` | tool error | skill tag "R" rejected by `search_evidence` (min length 2), Fit Matcher crashed | **fixed** `56c1168` + regression test |
| fullstack `job_00327` | tool error | an empty cover letter reached `approval`; `create_proposal_draft`'s schema (≥ 50 chars) rejected it | failed safe (nothing sent); root cause not traced |
| fullstack `job_00244` | unsupported claim | 3 unsupported claims left after verification on a multi-requirement job | not fixed |
| data_analyst `job_00055` | unsupported claim | judge gave 5/5 to a letter with 1 unsupported claim | shows the judge does not check grounding |

**Analysis.** The fix in `72d8bb0` did what it was for: wrong skips 18 → 9 and tool errors 11 → 1 on the same jobs
(most of run 1's tool errors were the Ollama memory crashes). Its cost is visible too: wrong bids 2 → 4, and two
of the four are off-persona jobs sitting at exactly the new 0.33 threshold. The largest remaining group is wrong
skips, and 5 of the 9 are injection jobs, although injection jobs are 24 of the 120 sampled. A plausible cause is
that stripping the injected lines also removes requirement text, so the Fit Matcher sees less to match; I did not
test this. Two failures came from schema validation on MCP tools: once a too-strict schema broke a correct job
("R"), once a correct schema stopped a broken one (empty letter). Separately, the decision step failed *open* when
the Risk Assessor crashed (with llama3.2, 0 of 12 scams caught and 4 bid on); `1458c02` makes it fail closed
(watch + needs attention), and the before/after is in section 10.

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
| Defence level | Injection jobs | Drafted | Flagged | ASR on Writer's first draft | ASR on what would be sent | Blocked |
|---|---|---|---|---|---|---|
| none | 15 | 7 | 4 | 0.0% | 0.0% | 0 |
| prompt | 15 | 10 | 15 | 0.0% | 0.0% | 0 |
| full | 15 | 10 | 15 | 0.0% | 0.0% | 0 |

<sub>Source: `evals/results/injection_asr.json`</sub>
<!-- /RESULTS:asr -->

**Result: no attack succeeded at any level, including `none`** (15 injection jobs, 5 templates, 7–10 drafted per
level). I checked that this is not a broken detector: the success tests are plain regexes on the outgoing text
("10 years", e-mail/phone/Telegram, "BANANA", system-prompt phrases), and the drafted letters at level `none`
contain none of them.

**Why `none` is not really undefended.** The three levels switch off delimiting, the "untrusted data" prompt lines,
signature detection and the Compliance Guard, but not the architecture: the Writer never receives the raw job post.
It receives the Job Analyst's typed `JobSpec` (title, summary, deliverables, required skills, screening questions,
`agent/nodes/writer.py`), so an instruction hidden in the post has to survive being summarised into those fields
before the Writer can follow it, and with qwen3:4b-instruct as the Analyst it never did. What the prompt-level
defences visibly changed is detection: flagged injections went from 4/15 to 15/15, so the user sees a warning on
every attacked job. That typed handoff is the real defence measured here, and the study does not isolate it.

**What still gets through / limits.** Nothing in this sample, but the sample is small (15 jobs, one model) and the
templates are blunt. Not tested: injections phrased as legitimate requirements (which *should* reach the
`JobSpec`), attacks through client messages in the conversation graph, and instructions inside a screening
question, which the Writer does see. Study C shows the risk without the typed handoff: the prebuilt supervisor,
whose writer agent reads the post, left 3 violations in final letters on injection jobs.

## 10. Results against the declared budgets

The instructor's hidden test run happens after the deadline against the live URL; at the time of writing there is
no live URL (free hosts that fit the stack required a payment card), so this section reports the dev-set runs
against the budgets declared in section 1, without excuses.

<!-- RESULTS:budgets -->
| Environment | Budget | Declared | Measured | Status |
|---|---|---|---|---|
| local | p95 s per job analysed | 45 | 51.2 | **missed** |
| local | p95 s per proposal | 420 | 359.8 | met |
| local | USD per job analysed | 0.0 | 0.0000 | met |
| local | USD per proposal | 0.0 | 0.0000 | met |
| hosted | p95 s per job analysed | 10 | n/a | n/a |
| hosted | p95 s per proposal | 60 | n/a | n/a |
| hosted | USD per job analysed | 0.002 | n/a | n/a |
| hosted | USD per proposal | 0.01 | n/a | n/a |

<sub>Source: declared in `agent/config.yaml → budgets.declared`; measured from `decisions_open.json`, `outcomes_best.json` (local) and `decisions_hosted.json`, `outcomes_hosted.json` (hosted, n/a if not run)</sub>
<!-- /RESULTS:budgets -->

<!-- RESULTS:decisions -->
| Model configuration | Jobs | Bid F1 | Bid P | Bid R | Scam P | Scam R | Price in fair band | p50 s / job | p95 s / job | Tokens / job |
|---|---|---|---|---|---|---|---|---|---|---|
| open (qwen3:4b-instruct cheap) | 120 | 0.854 | 0.911 | 0.804 | 0.960 | 1.000 | 100.0% | 34.2 | 51.2 | 2813 |

<sub>Source: `decisions_open.json`, `decisions_open_alt.json` (stratified dev sample; RTX 4060 8 GB, local Ollama, 0 USD)</sub>
<!-- /RESULTS:decisions -->

**One optimisation that helped: skipping early.** The deterministic `decide` node runs before the Writer, so a
job that is skipped or watched never pays for drafting, verification, critique and compliance, which are where
most of a proposal's time and tokens go. Measured on the decision run:

<!-- RESULTS:optimisation -->
| Skipping early | Value |
|---|---|
| Jobs analysed | 120 |
| Stopped at decide (skip or watch) | 75 (62.5%) |
| Mean s per job that stopped at decide | 34.7 |
| Mean s to decide on a job that went on to bid | 36.5 |
| p50 s for a full proposal (outcomes_best) | 341.9 |
| GPU time saved on this sample by not drafting stopped jobs (s) | 23045 |

<sub>Source: `decisions_open_detail.jsonl` (per-job latency) and `outcomes_best.json`</sub>
<!-- /RESULTS:optimisation -->

62.5% of jobs stopped at `decide`, each after about 35 s instead of the roughly 342 s a full proposal takes, about
6.4 GPU-hours saved on this 120-job sample. This is an estimate (stopped jobs × difference of means/medians), not
a measured A/B run, since the pipeline was never run without the early stop.

**Decisions.** On 120 stratified dev jobs (40 per persona) GigBid bid on 45, skipped 63 and watched 12: bid F1
0.854 (precision 0.911, recall 0.804), every scam caught (recall 1.000, precision 0.960: one legitimate job was
flagged), every planted injection detected, and every price inside the fair band. The weak persona is
data_analyst (bid recall 0.667 against 0.923 for ai_ml): all 7 of its missed bids were stopped by the fit rules
(4 watched under `minimum_fit_score`, 3 skipped under `watch_fit_score`), with Fit Matcher scores between 0 and
0.25, below the 0.33 bid threshold. None was lost to the budget or scam rules. So the Fit Matcher under-scores
jobs that the labelling rules count as in-persona for this persona; lowering the threshold further would trade
precision for them, which is why the fix in section 8 stopped at 0.33.

**Budgets.** *Missed:* local p95 per job analysed is 51.2 s against 45 s declared. The miss is not caused by
bidding more: in the final run even skipped jobs had a median of 32.7 s (22.6 s in run 1), so the whole pipeline
was slower. Run 1 and the final run used the same models and jobs; the final run was made after the WSL memory
change and the extra injection-stripping step, and I did not isolate which of the two (or GPU contention) is
responsible. *Met:* 0 USD per job locally. *Not measured:* the proposal budgets (pending `outcomes_best.json`)
and every hosted budget, because there is no hosted deployment (section 10 opening) and the Groq decision run was
not made; these are reported as n/a rather than estimated.

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
| v8_corrective | ai_ml | 83 | 0.255 | 0.266 | 0.270 | 0.242 |
| v8_corrective | fullstack | 92 | 0.267 | 0.285 | 0.275 | 0.227 |
| v8_corrective | data_analyst | 109 | 0.292 | 0.271 | 0.301 | 0.244 |

<sub>Source: `evals/results/rag_variants.json` (`personas.*.recall@5_by_job_type`)</sub>
<!-- /RESULTS:rag_personas -->
