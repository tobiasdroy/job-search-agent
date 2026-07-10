#!/usr/bin/env python3
"""Daily job search agent: fetch listings, rank via Gemini, email top 3."""
import json
import os
import re
import smtplib
import sys
import time
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
GEMINI_MODELS = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-2.0-flash"]

ADZUNA_APP_ID = os.environ["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = os.environ["ADZUNA_APP_KEY"]
REED_API_KEY = os.environ["REED_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SMTP_USERNAME = os.environ["SMTP_USERNAME"]
SMTP_APP_PASSWORD = os.environ["SMTP_APP_PASSWORD"]
EMAIL_TO = os.environ.get("EMAIL_TO", SMTP_USERNAME)

QUERIES = [
    "graduate data analyst",
    "graduate data scientist",
    "machine learning graduate",
    "junior data analyst",
    "data science trainee",
]


def fetch_adzuna():
    results = []
    for q in QUERIES:
        try:
            r = requests.get(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params={
                    "app_id": ADZUNA_APP_ID,
                    "app_key": ADZUNA_APP_KEY,
                    "results_per_page": 20,
                    "what": q,
                    "where": "London",
                    "content-type": "application/json",
                },
                timeout=20,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                results.append({
                    "source": "adzuna",
                    "title": j.get("title", ""),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "salary_min": j.get("salary_min"),
                    "salary_max": j.get("salary_max"),
                    "url": j.get("redirect_url", ""),
                    "description": j.get("description", ""),
                })
        except requests.RequestException as e:
            print(f"Adzuna query '{q}' failed: {e}", file=sys.stderr)
    return results


def fetch_reed():
    results = []
    for q in QUERIES:
        try:
            r = requests.get(
                "https://www.reed.co.uk/api/1.0/search",
                params={"keywords": q, "locationName": "London", "resultsToTake": 25},
                auth=(REED_API_KEY, ""),
                timeout=20,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                results.append({
                    "source": "reed",
                    "title": j.get("jobTitle", ""),
                    "company": j.get("employerName", ""),
                    "location": j.get("locationName", ""),
                    "salary_min": j.get("minimumSalary"),
                    "salary_max": j.get("maximumSalary"),
                    "url": j.get("jobUrl", ""),
                    "description": j.get("jobDescription", ""),
                })
        except requests.RequestException as e:
            print(f"Reed query '{q}' failed: {e}", file=sys.stderr)
    return results


def fetch_remoteok():
    try:
        r = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        r.raise_for_status()
        results = []
        for j in r.json():
            if not isinstance(j, dict) or "position" not in j:
                continue
            tags = " ".join(j.get("tags", [])).lower()
            if not any(k in tags for k in ["data", "analyst", "python", "ml", "machine-learning"]):
                continue
            results.append({
                "source": "remoteok",
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": "Remote",
                "salary_min": j.get("salary_min"),
                "salary_max": j.get("salary_max"),
                "url": j.get("url", ""),
                "description": (j.get("description") or "")[:1500],
            })
        return results
    except requests.RequestException as e:
        print(f"RemoteOK failed: {e}", file=sys.stderr)
        return []


def fetch_arbeitnow():
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=20)
        r.raise_for_status()
        results = []
        for j in r.json().get("data", []):
            location = j.get("location", "") or ""
            if "london" not in location.lower() and not j.get("remote"):
                continue
            results.append({
                "source": "arbeitnow",
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": location or ("Remote" if j.get("remote") else ""),
                "salary_min": None,
                "salary_max": None,
                "url": j.get("url", ""),
                "description": (j.get("description") or "")[:1500],
            })
        return results
    except requests.RequestException as e:
        print(f"Arbeitnow failed: {e}", file=sys.stderr)
        return []


def load_seen():
    path = BASE_DIR / "seen_jobs.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_seen(seen):
    (BASE_DIR / "seen_jobs.json").write_text(json.dumps(seen, indent=2) + "\n")


def call_gemini(prompt):
    """Try each model in GEMINI_MODELS, retrying transient failures (503/429) with backoff."""
    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            try:
                r = requests.post(
                    url,
                    params={"key": GEMINI_API_KEY},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=60,
                )
                if r.status_code in (429, 503):
                    last_error = f"{model}: {r.status_code} {r.text[:200]}"
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            except requests.RequestException as e:
                last_error = f"{model}: {e}"
                time.sleep(5 * (attempt + 1))
        print(f"Model {model} exhausted retries, trying next model", file=sys.stderr)
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def build_ranking_prompt(cv, prefs, candidates):
    listing_block = "\n\n".join(
        f"[{i}] {c['title']} | {c['company']} | {c['location']} | "
        f"salary: {c['salary_min']}-{c['salary_max']} | source: {c['source']}\n"
        f"{c['description'][:600]}"
        for i, c in enumerate(candidates)
    )
    return f"""You are helping rank job listings for a candidate. Read the CV and preferences below,
then review the numbered job listings and pick the best 3 matches (fewer if fewer than 3 are genuinely good).

Respond with ONLY a JSON array (no markdown fences, no other text) of up to 3 objects, ordered best first:
[{{"index": <int>, "why": "<2-4 sentences on why this fits, referencing specific CV experience>", "caveat": "<any notable caveat, or empty string>"}}]

=== CV ===
{cv}

=== PREFERENCES ===
{prefs}

=== JOB LISTINGS ===
{listing_block}
"""


def parse_gemini_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def send_email(picks, candidates):
    today = date.today().strftime("%-d %B %Y")
    msg = EmailMessage()
    msg["Subject"] = f"Your Daily Job Matches — {today}"
    msg["From"] = SMTP_USERNAME
    msg["To"] = EMAIL_TO

    if not picks:
        body = "No strong matches found today. Talk tomorrow.\n"
    else:
        lines = [f"Hi Tobias,\n\nHere are today's top {len(picks)} matches:\n"]
        for n, pick in enumerate(picks, 1):
            c = candidates[pick["index"]]
            salary = ""
            if c.get("salary_min") or c.get("salary_max"):
                salary = f" | Salary: {c.get('salary_min', '?')}-{c.get('salary_max', '?')}"
            lines.append(
                f"{n}. {c['title']} — {c['company']}\n"
                f"   Location: {c['location']}{salary}\n"
                f"   Link: {c['url']}\n"
                f"   Why: {pick.get('why', '')}\n"
                + (f"   Caveat: {pick.get('caveat')}\n" if pick.get("caveat") else "")
            )
        lines.append("\nThat's it for today — talk tomorrow.\n")
        body = "\n".join(lines)

    msg.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_APP_PASSWORD)
        smtp.send_message(msg)


def main():
    cv = (BASE_DIR / "CV.md").read_text()
    prefs = (BASE_DIR / "preferences.md").read_text()
    seen = load_seen()
    seen_urls = {s["url"] for s in seen}

    all_jobs = fetch_adzuna() + fetch_reed() + fetch_remoteok() + fetch_arbeitnow()
    print(f"Fetched {len(all_jobs)} total listings")

    candidates = [j for j in all_jobs if j["url"] and j["url"] not in seen_urls]
    # de-dupe within this run by URL
    dedup = {}
    for j in candidates:
        dedup[j["url"]] = j
    candidates = list(dedup.values())
    print(f"{len(candidates)} new candidates after filtering seen jobs")

    picks = []
    if candidates:
        prompt = build_ranking_prompt(cv, prefs, candidates)
        try:
            raw = call_gemini(prompt)
            picks = parse_gemini_json(raw)
            picks = [p for p in picks if 0 <= p.get("index", -1) < len(candidates)][:3]
        except Exception as e:
            print(f"Gemini ranking failed: {e}", file=sys.stderr)

    send_email(picks, candidates)
    print(f"Email sent with {len(picks)} picks")

    today_str = date.today().isoformat()
    for j in candidates:
        seen.append({
            "url": j["url"],
            "title": j["title"],
            "company": j["company"],
            "first_seen": today_str,
        })
    save_seen(seen)
    print(f"seen_jobs.json updated, now {len(seen)} entries")


if __name__ == "__main__":
    main()
