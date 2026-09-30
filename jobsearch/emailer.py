"""Optional daily digest email (off by default; toggled on the app's settings page)."""
import smtplib
from datetime import date
from email.message import EmailMessage

from .config import EMAIL_TO, SMTP_APP_PASSWORD, SMTP_USERNAME


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
