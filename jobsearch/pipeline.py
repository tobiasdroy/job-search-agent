"""Daily run: fetch → dedupe against the DB → rank with Gemini → store → optional email."""
import sys
import threading
from datetime import date

from . import db
from .config import BASE_DIR
from .emailer import send_email
from .fetchers import (
    BLOCKLISTED_COMPANIES, fetch_adzuna, fetch_arbeitnow, fetch_reed, fetch_remoteok,
    normalize_company_title, normalize_url,
)
from .ranking import MAX_PICKS, build_ranking_prompt, call_gemini, parse_gemini_json

_run_lock = threading.Lock()


def run_daily():
    """Returns a short human-readable summary. Only one run at a time."""
    if not _run_lock.acquire(blocking=False):
        return "A refresh is already running."
    try:
        db.set_meta("fetch_status", "running")
        summary = _run()
        db.set_meta("fetch_status", summary)
        return summary
    except Exception as e:
        db.set_meta("fetch_status", f"Failed: {e}")
        raise
    finally:
        _run_lock.release()


def _run():
    db.init_db()
    cv = (BASE_DIR / "CV.md").read_text()
    prefs = (BASE_DIR / "preferences.md").read_text()
    seen_urls, seen_keys = db.seen_keys()

    all_jobs = fetch_adzuna() + fetch_reed() + fetch_remoteok() + fetch_arbeitnow()
    print(f"Fetched {len(all_jobs)} total listings")

    def is_new(j):
        if not j["url"]:
            return False
        if normalize_url(j["url"]) in seen_urls:
            return False
        if normalize_company_title(j["title"], j["company"]) in seen_keys:
            return False
        company_clean = (j["company"] or "").strip().lower()
        if any(b in company_clean for b in BLOCKLISTED_COMPANIES):
            return False
        return True

    candidates = [j for j in all_jobs if is_new(j)]
    # de-dupe within this run by normalized URL and by company+title
    dedup = {}
    for j in candidates:
        dedup[normalize_url(j["url"])] = j
    candidates = list(dedup.values())
    dedup_by_key = {}
    for j in candidates:
        dedup_by_key[normalize_company_title(j["title"], j["company"])] = j
    candidates = list(dedup_by_key.values())
    print(f"{len(candidates)} new candidates after filtering seen jobs")

    picks = []
    today_str = date.today().isoformat()
    if candidates:
        prompt = build_ranking_prompt(
            cv, prefs, candidates,
            feedback=db.recent_feedback(limit=40),
            dismissed=db.recent_dismissed(),
        )
        print(f"Ranking prompt: {len(prompt)} chars, feedback section: {'=== PAST FEEDBACK ===' in prompt}")
        try:
            raw = call_gemini(prompt)
            picks = parse_gemini_json(raw)
            picks = [p for p in picks if 0 <= p.get("index", -1) < len(candidates)][:MAX_PICKS]
        except Exception as e:
            # leave candidates unstored so the next refresh retries them
            print(f"Gemini ranking failed: {e}", file=sys.stderr)
            return f"{len(candidates)} new listings found, but ranking failed ({e}). Try refreshing later."

    for p in picks:
        candidates[p["index"]].update(
            status="new", rank_date=today_str, score=p.get("score"),
            why=p.get("why", ""), caveat=p.get("caveat", ""),
        )
    # every candidate is stored (picked or not) so it's never re-surfaced
    db.insert_jobs(candidates, today_str)
    db.set_meta("last_fetch_at", db.now())

    if db.email_enabled():
        send_email(picks[:3], candidates)
        print("Email sent")

    return f"{len(all_jobs)} fetched, {len(candidates)} new, {len(picks)} recommended."
