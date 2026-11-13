#!/bin/bash
# Runs after scripts/eval_queue.sh: V8 (needs Ollama to itself) and the deploy-embedding cost measurement.
cd "$(dirname "$0")/.."
export PYTHONPATH=. MODEL_PROFILE=open
until grep -q QUEUE_DONE .logs/queue.status 2>/dev/null; do sleep 60; done
step() { name=$1; shift; echo "$(date +%H:%M) START $name" >> .logs/queue.status
  if "$@" > ".logs/$name.log" 2>&1; then echo "$(date +%H:%M) DONE $name" >> .logs/queue.status
  else echo "$(date +%H:%M) FAIL $name" >> .logs/queue.status; fi; }
step rag_v8_retry .venv/bin/python -m evals.runners.rag_eval --resume
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 step rag_deploy_embedding .venv/bin/python -m evals.runners.rag_eval \
  --variants v0_naive v5_multivector --out rag_deploy_embedding
echo "$(date +%H:%M) TAIL_DONE" >> .logs/queue.status
