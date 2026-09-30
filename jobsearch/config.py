"""Credentials and paths. Env vars win; config.json (gitignored) is the local fallback."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CV_DIR = BASE_DIR / "cv"
DB_PATH = BASE_DIR / "jobs.db"

_config_path = BASE_DIR / "config.json"
_cfg = json.loads(_config_path.read_text()) if _config_path.exists() else {}


def _get(env_name, *path, default=None):
    if os.environ.get(env_name):
        return os.environ[env_name]
    node = _cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


ADZUNA_APP_ID = _get("ADZUNA_APP_ID", "adzuna", "app_id")
ADZUNA_APP_KEY = _get("ADZUNA_APP_KEY", "adzuna", "app_key")
REED_API_KEY = _get("REED_API_KEY", "reed", "api_key")
GEMINI_API_KEY = _get("GEMINI_API_KEY", "gemini", "api_key")
SMTP_USERNAME = _get("SMTP_USERNAME", "smtp", "username")
SMTP_APP_PASSWORD = _get("SMTP_APP_PASSWORD", "smtp", "app_password")
EMAIL_TO = _get("EMAIL_TO", "smtp", "to", default=SMTP_USERNAME)
