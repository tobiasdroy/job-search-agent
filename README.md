# Job Search Agent

A personal job-search app that runs locally. It pulls UK graduate and early-career data & ML listings from several job-board APIs and ranks the new ones against my CV and preferences with Gemini. The results show up in a small web app. There I can leave feedback on each job, which is fed back into future rankings, and tailor my CV to any job with one click.

CV tailoring runs headless Claude Code (`claude -p`) inside `cv/`, so it uses my Claude subscription rather than API credits.

## How it works

```
Adzuna ─┐
Reed ───┤                                                 ┌─► web app (localhost:8765)
RemoteOK┼─► normalise ─► drop seen jobs ─► Gemini ranking ┤     ├─ feedback ──┐
Arbeitnow┘                    ▲               ▲           └─► email (off by default)
                              │               └──── past feedback + dismissals ◄┘
                          jobs.db (SQLite)
                                                     "Tailor CV" ─► claude -p in cv/ ─► cv/applications/<company>/
```

1. **Fetch**: `jobsearch/fetchers.py` has one fetcher per API. Each normalises results to a common shape and handles its own request failures.
2. **Dedupe**: `jobs.db` records every listing ever fetched. Candidates are filtered by normalised URL and by company + title, then de-duplicated within the run. A small company blocklist removes recurring low-quality posters.
3. **Rank**: the CV, preferences, recent feedback, recently dismissed jobs and the numbered listings go to Gemini in one prompt, which returns up to 10 scored picks. If ranking fails, the candidates are not marked as seen, so the next refresh retries them.
4. **Review**: the web app lists picks by day. Each job can be shortlisted, marked applied or dismissed, and given a free-text feedback note.
5. **Tailor**: "Tailor CV" (on a job, or with a pasted JD at `/tailor/new`) writes `jd.txt` and runs Claude Code in `cv/` under the rules in `cv/CLAUDE.md`. The result is a compiled, committed one-page PDF plus a summary of the changes and omitted keywords.

The app fetches automatically on startup if the last fetch is more than 20 hours old, and has a "Refresh now" button.

## Repository layout

| Path | Purpose |
| --- | --- |
| `jobsearch/web.py` | FastAPI app (routes); templates in `jobsearch/templates/` (Jinja2 + htmx) |
| `jobsearch/pipeline.py` | `run_daily()`: fetch → dedupe → rank → store → optional email |
| `jobsearch/fetchers.py` | Job-board API clients, dedup key normalisation |
| `queries.txt` | Search terms sent to Adzuna / Reed (editable in Settings) |
| `jobsearch/ranking.py` | Gemini client and prompts (ranking + feedback distillation) |
| `jobsearch/tailor.py` | Runs `claude -p` in `cv/` as a background job |
| `jobsearch/db.py` | SQLite schema and queries (`jobs.db`, gitignored) |
| `run_job_search.py` | CLI: one fetch/rank run without the web app |
| `cv/` | The CV personaliser; `cv/cv-base.tex` is the master CV, used for both ranking and tailoring (imported from `~/cv` via `git subtree`, history kept) |
| `preferences.md` | What I'm looking for; used for ranking, read fresh on every run (editable in Settings) |
| `config.json` | Local credentials (gitignored) |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn jobsearch.web:app --port 8765
```

Then open <http://localhost:8765>.

**Easier:** run `scripts/install-launcher.sh` once. It builds `~/Applications/Job Search.app`, which you can open from Spotlight or the Dock. It starts the server if it isn't already running and opens the page. To have the server running permanently from login instead, see `scripts/com.tobiasdroy.jobsearch.plist`.

CV tailoring also needs `claude` (logged in), `tectonic` and `pdftotext` on `PATH`. Make sure `ANTHROPIC_API_KEY` is not what you want billed: the app strips it from the subprocess env so Claude Code uses the subscription login.

### Credentials

Read from environment variables first, then from `config.json`:

| Env var | `config.json` path | Where to get it |
| --- | --- | --- |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | `adzuna.app_id`, `adzuna.app_key` | [Adzuna developer portal](https://developer.adzuna.com/) |
| `REED_API_KEY` | `reed.api_key` | [Reed developer API](https://www.reed.co.uk/developers) |
| `GEMINI_API_KEY` | `gemini.api_key` | [Google AI Studio](https://aistudio.google.com/) |
| `SMTP_USERNAME`, `SMTP_APP_PASSWORD`, `EMAIL_TO` | `smtp.username`, `smtp.app_password`, `smtp.to` | Gmail [app password](https://myaccount.google.com/apppasswords); only needed if email is enabled |

RemoteOK and Arbeitnow need no key.

## Customising

- **Change what gets recommended**: leave feedback on jobs. It goes straight into the next ranking prompt. To make it permanent, use Settings → "Distil feedback into preferences", which proposes an edit to `preferences.md` for you to review. `preferences.md` can also be edited directly.
- **Widen the search**: Settings → Search terms (`queries.txt`). Preferences can be edited in Settings too.
- **Suppress a noisy employer**: add a lowercase substring to `BLOCKLISTED_COMPANIES`.
- **Change how CVs are tailored**: edit `cv/CLAUDE.md`.
- **Email digest**: toggle it in Settings (off by default).
