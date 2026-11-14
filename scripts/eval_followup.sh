#!/usr/bin/env bash
# Runs after scripts/eval_queue.sh: V8 threshold calibration + rerun, and the V5 ablation (CPU heavy, so not run
# next to the GPU queue on a 12 GB WSL machine). Logs to .logs/followup.status.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
S=.logs/followup.status
while pgrep -f "[e]val_queue.sh" >/dev/null; do sleep 60; done
echo "$(date +%H:%M) START v8_calibrate" >> $S
$PY -m evals.runners.rag_v8_calibrate > .logs/rag_v8_calibrate.log 2>&1 || echo "$(date +%H:%M) FAIL v8_calibrate" >> $S
$PY - <<'PY' >> $S 2>&1
import json, re
b = json.load(open("evals/results/rag_v8_calibration.json"))["best_threshold"]["threshold"]
p = "agent/config.yaml"; s = open(p).read()
s = re.sub(r"corrective: \{relevant: [-\d.]+, weak: [-\d.]+,", f"corrective: {{relevant: {b}, weak: {round(b - 1.0, 1)},", s)
open(p, "w").write(s)
print("config corrective.relevant ->", b)
PY
echo "$(date +%H:%M) START v8_rerun" >> $S
$PY - <<'PY'
import json
p = "evals/results/rag_variants.json"; d = json.load(open(p)); d["results"].pop("v8_corrective", None); json.dump(d, open(p, "w"), indent=2)
PY
$PY -m evals.runners.rag_eval --variants v8_corrective --resume > .logs/rag_v8_rerun.log 2>&1 \
  && echo "$(date +%H:%M) DONE v8_rerun" >> $S || echo "$(date +%H:%M) FAIL v8_rerun" >> $S
echo "$(date +%H:%M) START ablation" >> $S
$PY -m evals.runners.rag_eval --ablate v5_multivector > .logs/rag_ablation.log 2>&1 \
  && echo "$(date +%H:%M) DONE ablation" >> $S || echo "$(date +%H:%M) FAIL ablation" >> $S
echo "$(date +%H:%M) END followup" >> $S
