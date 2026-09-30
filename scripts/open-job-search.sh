#!/bin/zsh
# Start the app if it isn't already running, then open it in the browser.
# Wrapped as ~/Applications/Job Search.app by scripts/install-launcher.sh.
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"   # claude, tectonic, pdftotext
URL=http://localhost:8765

if ! curl -s -o /dev/null "$URL"; then
  [ -x .venv/bin/uvicorn ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
  nohup .venv/bin/uvicorn jobsearch.web:app --port 8765 > /tmp/jobsearch.log 2>&1 &
  for _ in {1..40}; do curl -s -o /dev/null "$URL" && break; sleep 0.25; done
fi
open "$URL"
