# Daily Job Search Playbook

Run this playbook once, end to end, every time it's triggered. All paths below are relative to
`~/Desktop/Job Search Agent/`.

## 1. Load inputs
- Read `CV.md` for Tobias's background and skills.
- Read `preferences.md` for target roles, location, salary floor, company-size bias, and dealbreakers.
- Read `config.json` for API credentials.
- Read `seen_jobs.json` — a JSON array of previously surfaced postings, each with at least `url`,
  `title`, `company`, `first_seen`. Build a set of already-seen URLs to exclude from this run.

## 2. Pull fresh postings
Query each source below for roles matching `preferences.md` (data/ML/analyst-type, graduate/early-career,
London on-site or hybrid). Adjust query terms as needed — try a few variations (e.g. "data analyst",
"data scientist", "machine learning engineer", "graduate data") rather than a single fixed string.

- **Adzuna** (`config.json` → `adzuna.app_id` / `adzuna.app_key`, country `gb`):
  `GET https://api.adzuna.com/v1/api/jobs/gb/search/{page}?app_id={app_id}&app_key={app_key}&results_per_page=20&what={query}&where=London&content-type=application/json`
- **Reed** (`config.json` → `reed.api_key`, used as HTTP Basic Auth username with a blank password):
  `GET https://www.reed.co.uk/api/1.0/search?keywords={query}&locationName=London&resultsToTake=25`
  One of the largest UK job boards — good primary source alongside Adzuna for London graduate/data roles.
- **RemoteOK** (no key): `GET https://remoteok.com/api` — filter client-side for relevant tags
  (data, python, analytics, ml) since this is remote-only, treat as a lower-priority supplemental source
  given Tobias's on-site/hybrid preference.
- **Arbeitnow** (no key): `GET https://www.arbeitnow.com/api/job-board-api` — filter for London/UK and
  relevant roles.
- **Web search/fetch**: Use WebSearch for things the APIs are likely to miss — e.g. graduate schemes,
  early-career data/ML programmes at mid-sized London companies, and company career pages. Use WebFetch
  to pull full job descriptions when a listing's summary isn't enough to judge fit.

## 3. Filter
Drop any posting where:
- `url` already appears in `seen_jobs.json`, OR
- It clearly violates a hard preference from `preferences.md` (e.g. not London/hybrid/on-site, obviously
  a huge corporation or an early-stage startup if that's identifiable, salary clearly under £35k when stated).

Don't over-filter on soft signals (e.g. exact job title) — Tobias cares about the day-to-day work matching
his skills, not keyword-matching titles.

## 4. Score and select
Judge each remaining posting against `CV.md` and `preferences.md` holistically: relevance of the work to
data/numbers/computing skills, company size fit, location fit, salary fit, and overall attractiveness for
an early-career candidate. Pick the **top 3**. If fewer than 3 good candidates exist, send fewer rather than
padding with weak matches — note this in the email.

## 5. Compose the email
- Subject: `Your Daily Job Matches — {today's date, e.g. 7 July 2026}`
- For each of the top 3, include:
  - Job title, company, location
  - Salary (if available)
  - Direct link to the posting
  - 2–4 sentences on why it's a good fit, referencing specific CV experience/skills
  - Any notable caveat (e.g. "salary not listed", "slightly larger company than your usual preference")
- Keep the tone concise and practical — this is a daily scan, not a cover letter.

## 6. Send
Send the email via SMTP using the credentials in `config.json` → `smtp` (Gmail SMTP with an app
password: host `smtp.gmail.com`, port 587, STARTTLS). Send from and to `smtp.username`/`smtp.to`
(both tobias.droy@gmail.com). Do not use the Gmail MCP `create_draft` tool for this step — it only
creates drafts, it cannot send. A minimal approach is a Python `smtplib` one-liner script run via Bash,
building the message with `email.message.EmailMessage`, setting `Subject`/`From`/`To`, and calling
`smtp.starttls()` + `smtp.login(username, app_password)` + `smtp.send_message(msg)`.

## 7. Update state
Append every newly surfaced posting from this run (selected or not — anything that passed the Step 3
filter and was scored) to `seen_jobs.json`, with `url`, `title`, `company`, and `first_seen` (today's date).
This prevents re-scoring or re-recommending the same listing on future runs. Write the file back as a
single JSON array.
