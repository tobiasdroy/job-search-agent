"""CV tailoring via headless Claude Code (`claude -p`) running in cv/.

Running through the Claude Code CLI means this uses whatever account Claude Code is
logged into (i.e. the Claude subscription), and cv/CLAUDE.md supplies the tailoring
rules exactly as in an interactive session. ANTHROPIC_API_KEY is stripped from the
subprocess env so it can never silently fall back to API billing.
"""
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import db
from .config import CV_DIR

TIMEOUT_SECONDS = 15 * 60
ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep",
    "Bash(tectonic:*)", "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "Bash(mkdir:*)", "Bash(ls:*)",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
]

# one worker: runs queue up rather than racing each other on git commits
_executor = ThreadPoolExecutor(max_workers=1)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def pick_folder(company, role):
    """applications/<company>/, or <company>-<role> if a CV for that company already exists."""
    base = slug(company) or "company"
    folder = base
    if (CV_DIR / "applications" / folder).exists():
        folder = f"{base}-{slug(role)}" if slug(role) else base
        n = 2
        candidate = folder
        while (CV_DIR / "applications" / candidate).exists():
            candidate = f"{folder}-{n}"
            n += 1
        folder = candidate
    return f"applications/{folder}"


def cv_filename(company):
    return "TobiasDroyCV" + re.sub(r"[^A-Za-z0-9]", "", company) + ".tex"


def build_prompt(company, role, folder):
    return f"""Tailor my CV for this job, following every rule in CLAUDE.md.

Company: {company}
Role: {role or "(see job description)"}
Output folder: {folder}/
Output file: {folder}/{cv_filename(company)}
The job description is already saved at {folder}/jd.txt — read it from there (don't rewrite it).

When committing, stage and commit only {folder}/ (e.g. `git add {folder} && git commit -m "..." -- {folder}`).

End your final message with exactly these two Markdown sections:
## Changes
## Omitted keywords
"""


def start_tailor(company, role, jd_text, job_id=None):
    folder = pick_folder(company, role)
    run_id = db.create_tailor_run(job_id, company, role, folder, jd_text)
    _executor.submit(_run, run_id)
    return run_id


def _run(run_id):
    run = db.get_tailor_run(run_id)
    folder = CV_DIR / run["folder"]
    db.update_tailor_run(run_id, status="running", started_at=db.now())
    try:
        claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "jd.txt").write_text(run["jd_text"].strip() + "\n")

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(
            [claude, "-p", build_prompt(run["company"], run["role"], run["folder"]),
             "--output-format", "json", "--permission-mode", "acceptEdits",
             "--allowedTools", *ALLOWED_TOOLS],
            cwd=CV_DIR, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {"is_error": True, "result": proc.stdout}

        pdfs = sorted(folder.glob("TobiasDroyCV*.pdf"))
        ok = proc.returncode == 0 and not result.get("is_error") and pdfs
        db.update_tailor_run(
            run_id,
            status="done" if ok else "failed",
            pdf_path=str(pdfs[0].relative_to(CV_DIR)) if pdfs else None,
            summary=result.get("result", ""),
            log=(proc.stderr or "")[-5000:] + (
                f"\nduration {result.get('duration_ms', 0) / 1000:.0f}s, turns {result.get('num_turns')}"
                if "duration_ms" in result else ""
            ),
            finished_at=db.now(),
        )
    except subprocess.TimeoutExpired:
        db.update_tailor_run(run_id, status="failed", log="Timed out.", finished_at=db.now())
    except Exception as e:
        db.update_tailor_run(run_id, status="failed", log=repr(e), finished_at=db.now())
