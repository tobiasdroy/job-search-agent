"""Gemini ranking. Past feedback is fed back into the prompt so recommendations improve."""
import json
import re
import sys
import time

import requests

from .config import GEMINI_API_KEY

# stable, generally-available models last, so an overloaded preview model still falls back to something
GEMINI_MODELS = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]
MAX_PICKS = 10
# gemini-3-flash-preview "thinks" before answering: ~2 min for a full day of listings
TIMEOUT_SECONDS = 240


def call_gemini(prompt):
    """Try each model in GEMINI_MODELS, retrying transient failures (503/429) with backoff."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set (env var or gemini.api_key in config.json)")
    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            try:
                r = requests.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY},  # a header, so the key never appears in error URLs
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=TIMEOUT_SECONDS,
                )
                if r.status_code in (429, 503):
                    last_error = f"{model}: {r.status_code} {r.text[:200]}"
                    print(f"{model} attempt {attempt + 1}: HTTP {r.status_code}", file=sys.stderr)
                    time.sleep(5 * (attempt + 1))
                    continue
                if 400 <= r.status_code < 500:
                    # e.g. 404 for a retired model: retrying won't help, move to the next one
                    last_error = f"{model}: {r.status_code} {r.text[:200]}"
                    print(f"{model}: HTTP {r.status_code}, skipping model", file=sys.stderr)
                    break
                r.raise_for_status()
                data = r.json()
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            except requests.Timeout:
                # a timeout after 4 minutes won't go better on retry; try the next model
                last_error = f"{model}: timed out after {TIMEOUT_SECONDS}s"
                print(last_error, file=sys.stderr)
                break
            except requests.RequestException as e:
                last_error = f"{model}: {e}"
                print(f"{model} attempt {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        print(f"Model {model} exhausted retries, trying next model", file=sys.stderr)
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def format_feedback(feedback, dismissed):
    if not feedback and not dismissed:
        return ""
    lines = [
        "=== PAST FEEDBACK ===",
        "The candidate reviewed earlier recommendations and said what was wrong (or right) with them.",
        "Treat these as corrections to the preferences above and apply the same reasoning to new listings.",
    ]
    for f in feedback:
        snippet = re.sub(r"\s+", " ", f["description"] or "")[:200]
        lines.append(f"- {f['title']} | {f['company']}: \"{f['text']}\"\n  (listing: {snippet})")
    if dismissed:
        lines.append("\nRecently dismissed without comment: " + "; ".join(
            f"{d['title']} ({d['company']})" for d in dismissed
        ))
    return "\n".join(lines) + "\n"


def build_ranking_prompt(cv, prefs, candidates, feedback=(), dismissed=()):
    listing_block = "\n\n".join(
        f"[{i}] {c['title']} | {c['company']} | {c['location']} | "
        f"salary: {c['salary_min']}-{c['salary_max']} | source: {c['source']}\n"
        f"{c['description'][:600]}"
        for i, c in enumerate(candidates)
    )
    return f"""You are helping rank job listings for a candidate. Read the CV, preferences and past feedback below,
then review the numbered job listings and pick the best matches — up to {MAX_PICKS}, fewer if fewer are genuinely good.
Do not pad the list with weak matches.

Respond with ONLY a JSON array (no markdown fences, no other text), ordered best first:
[{{"index": <int>, "score": <0-100 fit score>, "why": "<2-4 sentences on why this fits, referencing specific CV experience>", "caveat": "<any notable caveat, or empty string>"}}]

=== CV ===
{cv}

=== PREFERENCES ===
{prefs}

{format_feedback(feedback, dismissed)}
=== JOB LISTINGS ===
{listing_block}
"""


def parse_gemini_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)



def build_distil_prompt(prefs, feedback):
    feedback_block = "\n".join(
        f"- {f['title']} | {f['company']}: \"{f['text']}\"" for f in feedback
    )
    return f"""Below is a job seeker's preferences file (Markdown) and the feedback they have left on
individual job recommendations. Rewrite the preferences file so it captures the lasting lessons from the
feedback (new dealbreakers, clarified priorities, companies/listing types to avoid, things they liked).

Rules:
- Keep the existing structure, headings and tone; edit or add bullets rather than rewriting from scratch.
- Only add what the feedback actually supports. Do not drop existing preferences unless feedback contradicts them.
- Generalise from individual jobs to rules where the feedback makes the pattern clear.
- Respond with ONLY the full new Markdown file, no fences or commentary.

=== CURRENT PREFERENCES ===
{prefs}

=== FEEDBACK ===
{feedback_block}
"""


def strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:\w+)?\s*", "", text)
    return re.sub(r"\s*```$", "", text) + "\n"
