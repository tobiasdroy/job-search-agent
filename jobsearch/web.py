"""Local web app: `uvicorn jobsearch.web:app --port 8765`."""
import difflib
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, pipeline, tailor
from .config import BASE_DIR, CV_DIR
from .fetchers import fetch_reed_description
from .ranking import build_distil_prompt, call_gemini, strip_fences

STALE_AFTER = timedelta(hours=20)
PREFS_PATH = BASE_DIR / "preferences.md"

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def start_refresh():
    threading.Thread(target=_safe_refresh, daemon=True).start()


def _safe_refresh():
    try:
        print(pipeline.run_daily())
    except Exception as e:
        print(f"Refresh failed: {e}")


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    db.fail_stale_tailor_runs()
    if db.get_meta("fetch_status") == "running":
        db.set_meta("fetch_status", "Previous refresh was interrupted.")
    last = db.get_meta("last_fetch_at")
    if not last or datetime.now() - datetime.fromisoformat(last) > STALE_AFTER:
        start_refresh()
    yield


app = FastAPI(lifespan=lifespan)


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


# ── jobs ──────────────────────────────────────────────────────────────────────

FILTERS = ["active", "new", "shortlisted", "applied", "dismissed", "manual", "all"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request, filter: str = "active"):
    if filter not in FILTERS:
        filter = "active"
    jobs = db.list_jobs(filter)
    groups = {}
    for j in jobs:
        groups.setdefault(j["rank_date"] or (j["first_seen"] or "")[:10], []).append(j)
    return render(request, "index.html", groups=groups, filter=filter, filters=FILTERS,
                  last_fetch=db.get_meta("last_fetch_at"), fetch_status=db.get_meta("fetch_status"))


@app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def set_status(request: Request, job_id: int, status: str = Form(...)):
    db.set_status(job_id, status)
    return render(request, "_card.html", j=_card_job(job_id))


@app.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def add_feedback(request: Request, job_id: int, text: str = Form(...), dismiss: str = Form("")):
    if text.strip():
        db.add_feedback(job_id, text.strip())
    if dismiss:
        db.set_status(job_id, "dismissed")
    return render(request, "_card.html", j=_card_job(job_id))


@app.get("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def list_feedback(request: Request, job_id: int):
    return render(request, "_feedback.html", feedback=db.job_feedback(job_id))


def _card_job(job_id):
    job = next((j for j in db.list_jobs("all") if j["id"] == job_id), None)
    if job is None:
        raise HTTPException(404)
    return job


@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    start_refresh()
    return render(request, "_refresh.html", status="running", last_fetch=db.get_meta("last_fetch_at"))


@app.get("/refresh/status", response_class=HTMLResponse)
def refresh_status(request: Request):
    return render(request, "_refresh.html", status=db.get_meta("fetch_status"),
                  last_fetch=db.get_meta("last_fetch_at"))


# ── tailoring ─────────────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}/tailor", response_class=HTMLResponse)
def tailor_form_for_job(request: Request, job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404)
    description = job["description"] or ""
    full = False
    if job["source"] == "reed":
        fetched = fetch_reed_description(job["url"])
        if fetched and len(fetched) > len(description):
            db.update_description(job_id, fetched)
            description, full = fetched, True
    return render(request, "tailor_form.html", job=job, company=job["company"], role=job["title"],
                  url=job["url"], jd=description, jd_is_full=full or job["source"] == "manual")


@app.get("/tailor/new", response_class=HTMLResponse)
def tailor_form_manual(request: Request):
    return render(request, "tailor_form.html", job=None, company="", role="", url="", jd="", jd_is_full=True)


@app.post("/tailor")
def tailor_start(company: str = Form(...), role: str = Form(""), jd: str = Form(...),
                 url: str = Form(""), job_id: str = Form("")):
    if job_id:
        job_id = int(job_id)
        db.update_description(job_id, jd)
    else:
        job_id = db.add_manual_job(company.strip(), role.strip(), url.strip(), jd)
    run_id = tailor.start_tailor(company.strip(), role.strip(), jd, job_id=job_id)
    return RedirectResponse(f"/tailor/{run_id}", status_code=303)


@app.get("/tailor", response_class=HTMLResponse)
def tailor_list(request: Request):
    return render(request, "tailor_list.html", runs=db.list_tailor_runs())


@app.get("/tailor/{run_id}", response_class=HTMLResponse)
def tailor_run(request: Request, run_id: int):
    run = db.get_tailor_run(run_id)
    if run is None:
        raise HTTPException(404)
    return render(request, "tailor_run.html", run=run)


@app.get("/tailor/{run_id}/status", response_class=HTMLResponse)
def tailor_run_status(request: Request, run_id: int):
    return render(request, "_run_status.html", run=db.get_tailor_run(run_id))


@app.get("/cv-files/{path:path}")
def cv_file(path: str):
    applications = (CV_DIR / "applications").resolve()
    target = (CV_DIR / path).resolve()
    if not target.is_relative_to(applications) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


# ── settings ──────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return render(request, "settings.html", email_enabled=db.email_enabled(),
                  feedback_count=len(db.recent_feedback()), proposal=None)


@app.post("/settings/email")
def settings_email(enabled: str = Form("")):
    db.set_meta("email_enabled", "1" if enabled else "0")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/distil", response_class=HTMLResponse)
def settings_distil(request: Request):
    current = PREFS_PATH.read_text()
    proposal = strip_fences(call_gemini(build_distil_prompt(current, db.recent_feedback())))
    diff = "\n".join(difflib.unified_diff(
        current.splitlines(), proposal.splitlines(), "preferences.md", "proposed", lineterm=""
    ))
    return render(request, "settings.html", email_enabled=db.email_enabled(),
                  feedback_count=len(db.recent_feedback()), proposal=proposal, diff=diff)


@app.post("/settings/preferences")
def settings_preferences(content: str = Form(...)):
    PREFS_PATH.write_text(content.replace("\r\n", "\n"))
    return RedirectResponse("/settings?saved=1", status_code=303)
