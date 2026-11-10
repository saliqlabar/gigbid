.PHONY: install dev test lint eval seed demo web e2e openapi compose smoke
PY ?= .venv/bin/python
export PYTHONPATH := .

install:
	uv venv .venv && uv pip install --python $(PY) -r requirements.lock
	cd apps/web && npm ci

seed:                       ## rebuild synthetic personas + GigSim seed + dev set from data/raw (Kaggle)
	$(PY) scripts/synth/personas.py --out data/personas --seed 4016
	$(PY) scripts/data/build_seed.py

dev:                        ## GigSim + API (inproc MCP) + web dev server; worker needs Redis (make compose)
	$(PY) -m uvicorn gigsim.app:app --port 8081 & \
	$(PY) -m uvicorn apps.api.main:app --port 8000 --reload & \
	cd apps/web && npm run dev

compose:
	docker compose -f infra/docker-compose.yml --env-file .env up --build

test:                       ## unit + integration + contract
	MODEL_PROFILE=test $(PY) -m pytest -q

e2e:                        ## Playwright (builds the web app, starts GigSim + API with the scripted model)
	cd apps/web && npx vite build && npx playwright test

lint:
	$(PY) -m ruff check .
	$(PY) scripts/ci/check_repo.py

openapi:                    ## regenerate the typed TS client from FastAPI's OpenAPI schema
	$(PY) scripts/export_openapi.py && cd apps/web && npm run gen:api

eval:                       ## retrieval + decision metrics + fixed GigSim reply-rate run -> evals/results/*.json
	$(PY) -m evals.runners.rag_eval
	$(PY) -m evals.runners.decision_eval
	$(PY) -m evals.runners.gigsim_outcomes
	$(PY) -m evals.runners.results_table

smoke:                      ## CI smoke eval: 30 jobs, scripted model, fails past thresholds
	MODEL_PROFILE=test $(PY) -m evals.runners.smoke_eval --jobs 30

demo:                       ## one GigSim job through the bid graph in the terminal
	$(PY) scripts/run_job.py --job job_00281 --persona fullstack
