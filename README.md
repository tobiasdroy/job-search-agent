# Job Search Agent

A personal daily job-search agent. Every morning it pulls UK graduate and early-career data & ML job listings from several job-board APIs, ranks the new ones against my CV and preferences using the Gemini API, and emails me the top 3 matches with a short explanation of why each fits (and any caveats).

It runs on GitHub Actions. There is no server, no build step, and no database.

## How it works

```
Adzuna ─┐
Reed ───┤                                   ┌─ top 3 picks ──► email digest
RemoteOK┼─► normalise ─► drop seen jobs ─► Gemini ranking ─┤
Arbeitnow┘                    ▲              └─ nothing/failed ► "no matches today" email
                              │
                        seen_jobs.json ◄── new candidates appended, committed by the workflow
```

1. **Fetch** – `fetch_adzuna`, `fetch_reed`, `fetch_remoteok` and `fetch_arbeitnow` each query one API and normalise results to a common shape (`source`, `title`, `company`, `location`, `salary_min`, `salary_max`, `url`, `description`). Each fetcher handles its own request failures, so one dead API doesn't stop the run.
2. **Dedupe** – `seen_jobs.json` records every listing ever surfaced. Candidates are filtered against it by normalised URL and by company + title (job boards rotate URLs and IDs for the same role), then de-duplicated within the run. A small company blocklist removes recurring low-quality posters.
3. **Rank** – the CV, preferences and numbered listings go to Gemini in a single prompt, which returns up to 3 picks as JSON (`index`, `why`, `caveat`). Models are tried in order (`gemini-3-flash-preview` → `gemini-flash-latest` → `gemini-2.0-flash`), with retries and backoff on HTTP 429/503.
4. **Email** – a plain-text digest is sent over Gmail SMTP (STARTTLS, app password). If ranking fails or there are no new listings, a short "no matches today" email is sent instead of silence.
5. **Persist** – every new candidate from the run, picked or not, is appended to `seen_jobs.json`, and the workflow commits and pushes it.

## Repository layout

| Path | Purpose |
| --- | --- |
| `run_job_search.py` | The whole agent; everything happens in `main()` |
| `CV.md` | CV used for ranking, read fresh on every run |
| `preferences.md` | What I do and don't want in a role, read fresh on every run |
| `seen_jobs.json` | Running memory of every job already surfaced (committed) |
| `.github/workflows/daily-job-search.yml` | Daily schedule (`7 7 * * *` UTC, roughly 8am London) plus manual trigger |
| `config.json` | Local credentials (gitignored, never committed) |

## Setup

### Requirements

- Python 3.12 (what the workflow uses)
- `requests` (`pip install requests`) – the only dependency

### API keys and credentials

| Variable | Where to get it |
| --- | --- |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | [Adzuna developer portal](https://developer.adzuna.com/) |
| `REED_API_KEY` | [Reed developer API](https://www.reed.co.uk/developers) |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) |
| `SMTP_USERNAME` | Gmail address that sends the digest |
| `SMTP_APP_PASSWORD` | Gmail [app password](https://myaccount.google.com/apppasswords) (requires 2-step verification) |
| `EMAIL_TO` | Optional – recipient, defaults to `SMTP_USERNAME` |

RemoteOK and Arbeitnow need no key.

### Run locally

```bash
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=... \
       GEMINI_API_KEY=... SMTP_USERNAME=... SMTP_APP_PASSWORD=...
python3 run_job_search.py
```

The script reads only environment variables, not `config.json`, so export the values into your shell first. Note that a local run sends a real email and updates `seen_jobs.json`.

### Run on GitHub Actions

Add each credential as a repository secret:

```bash
gh secret set ADZUNA_APP_ID -b"<value>" --repo tobiasdroy/job-search-agent
# repeat for ADZUNA_APP_KEY, REED_API_KEY, GEMINI_API_KEY, SMTP_USERNAME, SMTP_APP_PASSWORD
```

Then trigger or inspect runs:

```bash
gh workflow run daily-job-search.yml --repo tobiasdroy/job-search-agent
gh run list --repo tobiasdroy/job-search-agent --workflow=daily-job-search.yml
gh run watch <run-id> --repo tobiasdroy/job-search-agent --exit-status
```

## Customising

- **Change what gets recommended** – edit `CV.md` and `preferences.md`. They are the main levers. Role *titles* deliberately don't gate matches; the ranking prompt judges by the day-to-day work described in the listing, so filtering lives in the prompt rather than in keyword rules.
- **Widen the search** – add search terms to `QUERIES` in `run_job_search.py`. Adzuna and Reed require a query string; RemoteOK and Arbeitnow return full listings that are filtered client-side.
- **Suppress a noisy employer** – add a lowercase substring to `BLOCKLISTED_COMPANIES`.
- **Re-surface old jobs** – remove entries from `seen_jobs.json`.
