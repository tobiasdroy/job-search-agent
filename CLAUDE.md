# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal job-search web app for Tobias Droy, run locally on his Mac. It fetches UK graduate/early-career data & ML listings from several job-board APIs and ranks them against the master CV (`cv/cv-base.tex`), `preferences.md` and his past feedback using Gemini. The results are shown in a FastAPI + htmx app, where each job gets a feedback box and a "Tailor CV" button. Tailoring runs headless Claude Code in `cv/` on his Claude subscription. There is no build step and no test suite.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn jobsearch.web:app --port 8765 --reload   # the app
.venv/bin/python run_job_search.py                          # one fetch/rank run, no web app
```

Credentials come from env vars, falling back to `config.json` (gitignored, never commit it). See `jobsearch/config.py` for the key paths. `jobs.db` (SQLite, gitignored) holds all state.

## Architecture

- `jobsearch/pipeline.py` `run_daily()`: fetch (four fetchers in `fetchers.py`, each swallowing its own failures) → dedupe against `jobs.db` by normalised URL and company+title → rank with one Gemini call → insert every candidate (picked ones get `status='new'` + score/why/caveat, the rest `'seen'`) → optionally email. If ranking fails, candidates are *not* stored, so the next refresh retries them. The web app runs it on startup when `last_fetch_at` is more than 20h old, and via "Refresh now".
- `jobsearch/ranking.py`: `call_gemini` tries `GEMINI_MODELS` in order with backoff on 429/503 (the preview model overloads often). `build_ranking_prompt` includes a PAST FEEDBACK section (last 40 notes + recent dismissals). `build_distil_prompt` powers Settings → "Distil feedback into preferences", which proposes a `preferences.md` rewrite that the user reviews before saving.
- `jobsearch/tailor.py`: writes `cv/<folder>/jd.txt`, then runs `claude -p ... --output-format json --permission-mode acceptEdits --allowedTools ...` with `cwd=cv/`, so `cv/CLAUDE.md` provides the tailoring rules. `ANTHROPIC_API_KEY` is stripped from the env so it bills the subscription, not the API. Runs are serialised on a one-worker executor and tracked in `tailor_runs`. If `applications/<company>/` already exists, the folder gets a `-<role>` suffix.
- `cv/` was imported from `~/cv` with `git subtree`. Its `CLAUDE.md` governs CV edits (never edit `cv-base.tex`, never invent experience).

## Editing behavior without touching code

The master CV `cv/cv-base.tex` (also the base for tailoring — there is no separate ranking CV), `preferences.md` and in-app feedback are the levers for changing recommendations. Prefer them over hardcoded filtering. Role *title* shouldn't gate matches; ranking follows day-to-day work content, which is why filtering happens in the Gemini prompt rather than in keyword rules. `queries.txt` (one term per line, editable in Settings alongside `preferences.md`) sets the Adzuna/Reed search surface; a role type missing there is never fetched.
