#!/bin/bash
# Runs every GPU-bound evaluation strictly one after another (8 GB VRAM: two at once got Ollama OOM-killed).
# Each step logs to .logs/<step>.log and appends DONE/FAIL to .logs/queue.status. Safe to re-run after a crash:
# a step whose results file already exists is skipped, and runners that can resume (decisions, Study C,
# injection, RAG) continue from their detail files.
cd "$(dirname "$0")/.."
export PYTHONPATH=. MODEL_PROFILE=${MODEL_PROFILE:-open}
PY=.venv/bin/python
R=evals/results
step() { name=$1; out=$2; shift 2
  if [ -n "$out" ] && [ -f "$R/$out.json" ]; then echo "$(date +%H:%M) SKIP $name ($out.json exists)" >> .logs/queue.status; return; fi
  echo "$(date +%H:%M) START $name" >> .logs/queue.status
  if "$@" > ".logs/$name.log" 2>&1; then echo "$(date +%H:%M) DONE $name" >> .logs/queue.status
  else echo "$(date +%H:%M) FAIL $name" >> .logs/queue.status; fi; }

step rag_v8 "" $PY -m evals.runners.rag_eval --resume
BEST=$($PY -c "import json;r=json.load(open('$R/rag_variants.json'))['results'];print(max(r,key=lambda k:(r[k]['overall']['recall@5'],r[k]['overall']['ndcg@10'])))")
echo "$(date +%H:%M) BEST_VARIANT $BEST" >> .logs/queue.status
export RAG_VARIANT=$BEST
step decisions_open decisions_open $PY -m evals.runners.decision_eval --per-persona 40 --out decisions_open
MODEL_PROFILE=open_alt step decisions_open_alt decisions_open_alt $PY -m evals.runners.decision_eval --per-persona 20 --out decisions_open_alt
step agency agency_eval $PY -m evals.runners.agency_eval --per-persona 15
step outcomes_v0 outcomes_v0 $PY -m evals.runners.gigsim_outcomes --per-persona 5 --variant v0_naive --out outcomes_v0
step outcomes_best outcomes_best $PY -m evals.runners.gigsim_outcomes --per-persona 5 --variant $BEST --out outcomes_best
step judge_labels_csv "" bash -c "[ -f evals/datasets/judge_labels.csv ] || $PY -m evals.runners.judge_calibration prepare"
step study_c study_c $PY -m evals.runners.study_c --jobs 50
step injection_asr injection_asr $PY -m evals.runners.injection_asr --per-persona 5
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 step rag_deploy_embedding rag_deploy_embedding $PY -m evals.runners.rag_eval \
  --variants v0_naive $BEST --out rag_deploy_embedding
step change_request change_request $PY -m evals.change_request.run
step failure_analysis failure_analysis $PY -m evals.runners.failure_analysis
echo "$(date +%H:%M) QUEUE_DONE" >> .logs/queue.status
