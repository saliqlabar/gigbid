#!/bin/bash
# Runs every GPU-bound evaluation strictly one after another (8 GB VRAM / ~8 GB WSL RAM: two at once got
# Ollama OOM-killed). Each step logs to .logs/<step>.log and appends DONE/FAIL to .logs/queue.status.
cd "$(dirname "$0")/.."
export PYTHONPATH=. MODEL_PROFILE=${MODEL_PROFILE:-open}
PY=.venv/bin/python
step() { name=$1; shift; echo "$(date +%H:%M) START $name" >> .logs/queue.status
  if "$@" > ".logs/$name.log" 2>&1; then echo "$(date +%H:%M) DONE $name" >> .logs/queue.status
  else echo "$(date +%H:%M) FAIL $name" >> .logs/queue.status; fi; }

while pgrep -f "[e]vals.runners.rag_eval" >/dev/null; do sleep 20; done
step rag_v8 $PY -m evals.runners.rag_eval --resume
BEST=$($PY -c "import json;r=json.load(open('evals/results/rag_variants.json'))['results'];print(max(r,key=lambda k:(r[k]['overall']['recall@5'],r[k]['overall']['ndcg@10'])))")
echo "$(date +%H:%M) BEST_VARIANT $BEST" >> .logs/queue.status
export RAG_VARIANT=$BEST
step decisions_open $PY -m evals.runners.decision_eval --per-persona 40 --out decisions_open --fresh
MODEL_PROFILE=open_alt step decisions_open_alt $PY -m evals.runners.decision_eval --per-persona 20 --out decisions_open_alt --fresh
step agency $PY -m evals.runners.agency_eval --per-persona 15
step outcomes_v0 $PY -m evals.runners.gigsim_outcomes --per-persona 5 --variant v0_naive --out outcomes_v0
step outcomes_best $PY -m evals.runners.gigsim_outcomes --per-persona 5 --variant $BEST --out outcomes_best
step study_c $PY -m evals.runners.study_c --jobs 50 --fresh
step injection_asr $PY -m evals.runners.injection_asr --per-persona 5 --fresh
echo "$(date +%H:%M) QUEUE_DONE" >> .logs/queue.status
