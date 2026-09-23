# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal daily job-search agent for Tobias Droy. A single Python script fetches UK graduate/early-career data & ML job listings from several job-board APIs, ranks them against `CV.md` and `preferences.md` using the Gemini API, and emails the top 3 matches. It runs automatically once a day via GitHub Actions — there is no local server, no build step, and no test suite.

## Commands

There is no package manifest, linter, or test suite in this repo. The only dependency is `requests`, installed ad hoc by the workflow (`pip install requests`).

**Run the agent locally** (all env vars below are required except `EMAIL_TO`):

```bash
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=... GEMINI_API_KEY=... SMTP_USERNAME=... SMTP_APP_PASSWORD=...
python3 run_job_search.py
```

Credentials for local runs also live in `config.json` (gitignored, never committed) — the script itself reads only from environment variables, so export from `config.json` manually or source them into your shell before running.

**Trigger / inspect the scheduled GitHub Actions run:**

```bash
gh workflow run daily-job-search.yml --repo tobiasdroy/job-search-agent
gh run list --repo tobiasdroy/job-search-agent --workflow=daily-job-search.yml
gh run watch <run-id> --repo tobiasdroy/job-search-agent --exit-status
```

Secrets used by the workflow (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REED_API_KEY`, `GEMINI_API_KEY`, `SMTP_USERNAME`, `SMTP_APP_PASSWORD`) are stored as GitHub Actions repo secrets, set via `gh secret set <NAME> -b"<value>" --repo tobiasdroy/job-search-agent`.

## Architecture

Everything happens in `run_job_search.py`'s `main()`, executed daily by `.github/workflows/daily-job-search.yml` (cron `7 7 * * *` UTC ≈ 8am London; also triggerable via `workflow_dispatch`). Data flow:

1. **Fetch** — four independent fetcher functions (`fetch_adzuna`, `fetch_reed`, `fetch_remoteok`, `fetch_arbeitnow`) each hit a different job-board API and normalize results into a common dict shape (`source`, `title`, `company`, `location`, `salary_min`, `salary_max`, `url`, `description`). Each fetcher swallows its own request failures so one dead API doesn't kill the run.
2. **Dedupe** — `seen_jobs.json` (committed to the repo, not gitignored) is the running memory of every URL ever surfaced. New candidates are filtered against it, then de-duplicated against each other within the same run.
3. **Rank** — if there are any new candidates, `build_ranking_prompt` feeds the CV, preferences, and numbered listings to Gemini in one call, asking for a JSON array (`index`, `why`, `caveat`) of up to 3 picks. `call_gemini` tries `GEMINI_MODELS` in order (`gemini-3-flash-preview` → `gemini-flash-latest` → `gemini-2.0-flash`), retrying each up to 3 times with backoff on HTTP 429/503 — this exists because the preview model is prone to transient overload errors, not because of anything wrong with the request.
4. **Email** — `send_email` composes a plain-text digest and sends it via Gmail SMTP (STARTTLS, app password auth) to `EMAIL_TO` (defaults to `SMTP_USERNAME`). If ranking failed or there were no new candidates, it sends a short "no matches today" email rather than staying silent.
5. **Persist** — every new candidate seen this run (selected or not) is appended to `seen_jobs.json` so it's never re-surfaced; the workflow commits and pushes this file as its final step.

## Editing behavior without touching code

`CV.md` and `preferences.md` are read fresh on every run and are the actual levers for changing what gets recommended — update these rather than hardcoding filtering logic into the script. `preferences.md` in particular encodes that role *title* shouldn't gate matches; ranking should follow day-to-day work content, which is why filtering happens via the Gemini prompt rather than keyword rules in Python.

`QUERIES` in `run_job_search.py` is the fixed set of search terms sent to Adzuna and Reed (both require query strings, unlike RemoteOK/Arbeitnow which return full listings to filter client-side) — add to this list to widen the search surface.
