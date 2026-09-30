"""Job-board fetchers. Each normalizes into a common dict shape and swallows its own
request failures so one dead API doesn't kill the run."""
import re
import sys
from urllib.parse import urlparse

import requests

from .config import ADZUNA_APP_ID, ADZUNA_APP_KEY, BASE_DIR, REED_API_KEY

QUERIES_PATH = BASE_DIR / "queries.txt"


def load_queries():
    """Search terms sent to Adzuna and Reed, one per line in queries.txt (editable in Settings)."""
    lines = QUERIES_PATH.read_text().splitlines() if QUERIES_PATH.exists() else []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def fetch_adzuna():
    results = []
    for q in load_queries():
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
    for q in load_queries():
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


BLOCKLISTED_COMPANIES = {"consula"}


def normalize_company_title(title, company):
    """Key on cleaned company+title so a repost under a rotated job ID/URL
    (e.g. Consula Group re-listing the same role daily) still counts as seen."""
    def clean(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{clean(company)}|{clean(title)}"


def normalize_url(url):
    """Collapse a listing URL to a stable dedup key. Adzuna rotates both a
    tracking query string AND the path shape (/jobs/details/<id> vs
    /jobs/land/ad/<id>) on every API call for the same underlying job, so
    stripping the query alone isn't enough — key on domain + numeric job id
    when the last path segment is one."""
    base = url.split("?", 1)[0].rstrip("/")
    last_segment = base.rsplit("/", 1)[-1]
    if last_segment.isdigit():
        return f"{urlparse(base).netloc}/{last_segment}"
    return base



def fetch_reed_description(url):
    """Reed search results truncate descriptions; the details endpoint returns the full text."""
    job_id = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if not job_id.isdigit():
        return None
    try:
        r = requests.get(
            f"https://www.reed.co.uk/api/1.0/jobs/{job_id}",
            auth=(REED_API_KEY, ""),
            timeout=20,
        )
        r.raise_for_status()
        html = r.json().get("jobDescription") or ""
    except requests.RequestException as e:
        print(f"Reed details {job_id} failed: {e}", file=sys.stderr)
        return None
    text = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
