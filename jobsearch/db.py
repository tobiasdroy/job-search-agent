"""SQLite storage (jobs.db, gitignored). Replaces seen_jobs.json as the dedup memory."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from .config import BASE_DIR, DB_PATH
from .fetchers import normalize_company_title, normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    url_key TEXT UNIQUE NOT NULL,
    company_title_key TEXT NOT NULL,
    source TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    salary_min REAL,
    salary_max REAL,
    url TEXT,
    description TEXT,
    first_seen TEXT,
    rank_date TEXT,
    score INTEGER,
    why TEXT,
    caveat TEXT,
    -- seen: fetched but not picked | new: ranked pick, unreviewed | manual: added by hand
    status TEXT NOT NULL DEFAULT 'seen'
);
CREATE INDEX IF NOT EXISTS jobs_ct ON jobs(company_title_key);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tailor_runs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    company TEXT NOT NULL,
    role TEXT,
    folder TEXT NOT NULL,
    jd_text TEXT NOT NULL,
    status TEXT NOT NULL,
    pdf_path TEXT,
    summary TEXT,
    log TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

STATUSES = ("seen", "new", "shortlisted", "applied", "dismissed", "manual")


def now():
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
    migrate_seen_jobs()


def migrate_seen_jobs():
    """One-off: import the legacy seen_jobs.json so historical dedup survives."""
    path = BASE_DIR / "seen_jobs.json"
    if not path.exists() or get_meta("seen_jobs_migrated"):
        return
    seen = json.loads(path.read_text())
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs (url_key, company_title_key, title, company, url, first_seen, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'seen')",
            [
                (normalize_url(s["url"]), normalize_company_title(s.get("title", ""), s.get("company", "")),
                 s.get("title", ""), s.get("company", ""), s["url"], s.get("first_seen"))
                for s in seen
            ],
        )
    set_meta("seen_jobs_migrated", now())
    print(f"Migrated {len(seen)} entries from seen_jobs.json")


def get_meta(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def email_enabled():
    return get_meta("email_enabled", "0") == "1"


# ── jobs ──────────────────────────────────────────────────────────────────────

def seen_keys():
    with connect() as conn:
        rows = conn.execute("SELECT url_key, company_title_key FROM jobs").fetchall()
    return {r["url_key"] for r in rows}, {r["company_title_key"] for r in rows}


def insert_jobs(jobs, first_seen):
    """Insert fetched candidates (dicts with optional score/why/caveat/status). Returns their ids."""
    ids = []
    with connect() as conn:
        for j in jobs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs (url_key, company_title_key, source, title, company, location, "
                "salary_min, salary_max, url, description, first_seen, rank_date, score, why, caveat, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (normalize_url(j["url"]), normalize_company_title(j["title"], j["company"]), j["source"],
                 j["title"], j["company"], j["location"], j.get("salary_min"), j.get("salary_max"),
                 j["url"], j["description"], first_seen, j.get("rank_date"), j.get("score"),
                 j.get("why"), j.get("caveat"), j.get("status", "seen")),
            )
            ids.append(cur.lastrowid)
    return ids


def add_manual_job(company, title, url, description):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (url_key, company_title_key, source, title, company, location, url, description, "
            "first_seen, status) VALUES (?, ?, 'manual', ?, ?, '', ?, ?, ?, 'manual')",
            (f"manual:{uuid.uuid4().hex}", normalize_company_title(title, company), title, company,
             url, description, now()),
        )
        return cur.lastrowid


def get_job(job_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(status_filter):
    """Ranked picks (and manual jobs) for the dashboard. status_filter: a status, 'active', or 'all'."""
    where = "WHERE (j.score IS NOT NULL OR j.status = 'manual')"
    args = ()
    if status_filter == "active":
        where += " AND j.status IN ('new', 'shortlisted', 'manual')"
    elif status_filter != "all":
        where += " AND j.status = ?"
        args = (status_filter,)
    with connect() as conn:
        return conn.execute(
            f"""SELECT j.*,
                   (SELECT COUNT(*) FROM feedback f WHERE f.job_id = j.id) AS feedback_count,
                   (SELECT id FROM tailor_runs t WHERE t.job_id = j.id ORDER BY t.id DESC LIMIT 1) AS last_tailor_id
                FROM jobs j {where}
                ORDER BY COALESCE(j.rank_date, j.first_seen) DESC, j.score DESC""",
            args,
        ).fetchall()


def set_status(job_id, status):
    if status not in STATUSES:
        raise ValueError(status)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def update_description(job_id, description):
    with connect() as conn:
        conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))


# ── feedback ──────────────────────────────────────────────────────────────────

def add_feedback(job_id, text):
    with connect() as conn:
        conn.execute("INSERT INTO feedback (job_id, text, created_at) VALUES (?, ?, ?)", (job_id, text, now()))


def job_feedback(job_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM feedback WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()


def recent_feedback(limit=None):
    sql = """SELECT f.text, f.created_at, j.title, j.company, j.description
             FROM feedback f JOIN jobs j ON j.id = f.job_id ORDER BY f.id DESC"""
    with connect() as conn:
        if limit:
            return conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
        return conn.execute(sql).fetchall()


def recent_dismissed(limit=30):
    with connect() as conn:
        return conn.execute(
            "SELECT title, company FROM jobs WHERE status = 'dismissed' ORDER BY rank_date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


# ── tailor runs ───────────────────────────────────────────────────────────────

def create_tailor_run(job_id, company, role, folder, jd_text):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tailor_runs (job_id, company, role, folder, jd_text, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (job_id, company, role, folder, jd_text, now()),
        )
        return cur.lastrowid


def update_tailor_run(run_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE tailor_runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def get_tailor_run(run_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM tailor_runs WHERE id = ?", (run_id,)).fetchone()


def list_tailor_runs(limit=50):
    with connect() as conn:
        return conn.execute("SELECT * FROM tailor_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def fail_stale_tailor_runs():
    """Runs left queued/running by a previous server process will never finish."""
    with connect() as conn:
        conn.execute(
            "UPDATE tailor_runs SET status = 'failed', log = COALESCE(log, '') || '\nServer restarted mid-run.' "
            "WHERE status IN ('queued', 'running')"
        )
