# Data Card

GigBid's development and evaluation data combines **real public job-market data from Kaggle** with
**clearly labelled synthetic data**. The instructor-provided GigSim, persona corpora and dev set were
not part of the starter kit we received, so the instructor's note allowed combining the Kaggle datasets
below with realistic synthetic data. Everything here is reproducible:

```bash
python scripts/synth/personas.py        # synthetic persona corpora -> data/personas/
python scripts/data/build_seed.py       # GigSim seed + dev set   -> gigsim/seed/, evals/datasets/
```

Raw Kaggle files live in `data/raw/` and are **git-ignored**; only derived, small files are committed.

## 1. Sources

| # | Dataset (Kaggle) | Used? | What we use it for | Licence |
|---|---|---|---|---|
| 1 | `asaniczka/upwork-job-postings-dataset-2024-50k-records` | Yes | 1,500 sampled **historical jobs** (title, fixed budget / hourly range, date) behind `GET /market/stats` for the Pricing Strategist | _confirm on the dataset page_ |
| 2 | `asaniczka/all-jobs-on-upwork-200k-plus` | No | Excluded: title + budget only (no descriptions or skills), overlaps dataset 1 | _confirm_ |
| 3 | `ahmedmyalo/upwork-freelance-jobs-60k` | Yes | **Live job stream**: title, description, skill tags (Category_1..9), fixed/hourly budget, Connects cost, experience level, and the client fields (spend, rating, reviews, payment verification, country, enterprise) | _confirm_ |
| 4 | `asaniczka/freelance-contracts-dataset-1-3-million-entries` | Yes | Real **paid hourly rates** by job-title keyword (AI/ML n=6,565; web; data analysis), used for fair hourly price bands → `gigsim/seed/market_rates.json` | _confirm_ |
| 5 | `sarveshchhetri/freelancer-success-earnings-and-churn-prediction` | Yes (light) | Median hourly rate by primary skill, a sanity reference for the persona rate floors | _confirm_ |
| 6 | `adityammantri/trial-1` (fake-job-posting set, `fraudulent` label) | Yes (light) | Fraudulent-posting phrasing ("earn 5000/week, immediate hiring, contact now…") mixed into synthetic scam jobs | _confirm_ |

## 2. Preprocessing (real data)

- **Stream (dataset 3):** drop duplicate `Job_URL`s and rows with missing title/description; keep
  descriptions > 120 chars; HTML-unescape text; parse `Job_Cost` ("$2,500") into a fixed budget
  and `Hourly_Rate` ("$35.00-$100.00") into `hourly_low/high`. Skill tags are the non-empty `Category_*` columns.
- **Persona routing:** each job's skill tags are mapped through a hand-written concept map
  (`CONCEPTS` in `build_seed.py`, e.g. `Microsoft Power BI → Power BI`, `Machine Learning →
  scikit-learn / XGBoost / PyTorch`). Generic tags (Python, JavaScript, Web Development, …) count 0.3.
  A job belongs to the persona with the highest score ≥ 1, otherwise to `other`.
- **Sampling:** 170 jobs per persona + 260 `other` jobs (seed 4016).
- **Clients:** real client fields are kept. Kaggle has no client id, so we group jobs into clients
  by hashing (country, spend, rating, reviews, payment status).
- **Historical (dataset 1):** random 1,500 rows; category from title/description keywords.
- **Contracts (dataset 4):** hourly rates kept within $3–$300; quartiles per persona keyword set.

## 3. Synthetic data (and how to tell it apart)

| Synthetic artefact | Where | Marker |
|---|---|---|
| 3 freelancer personas (CV PDF/MD, 17–20 case studies, repo README + code per project, reviews, 150 past proposals with outcomes) | `data/personas/*` | `SYNTHETIC DATA` header in every file; `"synthetic": true` in JSON; display names end in "(synthetic)" |
| Scam jobs (90): real job text + off-platform / unpaid-trial / upfront-fee text + trial_1 phrasing; brand-new unverified client | `gigsim/seed/jobs.json` | `_synthetic.scam = true`, `source = "synthetic scam built on …"` |
| Planted prompt injections (75) appended to real in-persona jobs | same | `_synthetic.injection = true` |
| Duplicate reposts (45) with edited titles | same | `_synthetic.duplicate_of` |
| Screening questions | same | `_synthetic.screening_questions = true` |
| Client ids, `hire_rate`, `jobs_posted`, `member_since`, hidden preferences | `gigsim/seed/clients.json` | `_synthetic`; hidden fields start with `_` and are never returned by the API |
| All gold labels | `evals/datasets/dev_*.jsonl` | `label_source` field |

Fields starting with `_` are stripped by GigSim before any response, so agents cannot see labels.

**Messiness by design (persona corpora):** each project is described three ways (case study,
README, code). About 55% of skill mentions in case studies are *implied* ("a Redis-backed background
task queue" instead of "Celery"). About 25% of READMEs are near-empty ("Internal tool. See code."),
so only the code imports reveal the stack. Past-proposal outcomes depend on writing quality.
In the generator, `score = 0.15 + 0.35·specific_opening + 0.25·cites_evidence + 0.15·concise ± 0.15`,
with `won > 0.75`, `replied > 0.5`, otherwise `ignored`. Outcome-aware retrieval (V4) has something real to learn from.

## 4. Gold labels (dev set: 200 jobs per persona)

Each persona's dev set holds 130 in-persona jobs, 45 off-persona jobs and 25 scams. Some in-persona
jobs carry planted injections. Labels are **rule-derived, not human-annotated**:

- `bid` = not a scam **and** in-persona **and** budget OK. Budget OK means fixed `budget_max` ≥ the persona's
  minimum project budget, or `hourly_high` ≥ 0.6 × the persona's rate.
- `gold_evidence` = case/readme/code ids of the top-4 persona projects by skill overlap with the
  job's mapped skills. Grade 2 for the top 2 projects, grade 1 for the next 2. These grades feed nDCG.
- `fair_price_band`: fixed jobs use 0.75–1.0 × the posted budget (market IQR if none is posted);
  hourly jobs use the posted range clipped to the real contract-rate IQR for the persona (dataset 4).
- `job_type` ∈ {scam, injection, multi_requirement (≥ 4 mapped skills), in_persona, off_persona}, used for breakdowns.

## 5. Limitations

- Labels come from our own rules, so a system can partly "learn the labeller". The report treats
  dev-set scores as relative comparisons between variants, not absolute quality.
- The concept map is hand-written and incomplete. Jobs whose tags we did not map count as off-persona.
- Kaggle jobs were scraped in 2023–2024 by the dataset authors. We do **no scraping ourselves**, and
  links to upwork.com are dropped.
- Client grouping by hashed attributes can merge or split real clients.
- Scam and injection jobs are template-based and easier than real adversarial posts.
- Synthetic personas are cleaner than real freelancer portfolios.
- The fake-job set (dataset 6) is about employment ads, not freelance gigs. We only borrow its phrasing.

## 6. Handling

No real personal data is used. Kaggle job text may contain names or emails written by the original
posters, so logs and traces pass through PII masking (see `docs/RUNBOOK.md`). Raw Kaggle files are
never committed, and the hidden test set never enters the repository.
