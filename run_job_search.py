#!/usr/bin/env python3
"""Fetch, rank and store today's jobs without the web app (e.g. from launchd/cron)."""
from jobsearch.pipeline import run_daily

if __name__ == "__main__":
    print(run_daily())
