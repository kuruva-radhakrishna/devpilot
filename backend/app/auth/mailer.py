"""Transactional email over HTTPS (Brevo or Resend).

HTTPS APIs rather than SMTP on purpose: Render's free tier blocks outbound SMTP
ports, and an API key is simpler to rotate than SMTP credentials. Configure ONE of:

  BREVO_API_KEY   + MAIL_FROM   -> https://brevo.com   (free: 300 emails/day; verify
                                   your sender address once, no domain required)
  RESEND_API_KEY  + MAIL_FROM   -> https://resend.com  (free tier only delivers to
                                   your own address until you verify a domain)

Failures are logged (never the key) and reported to the caller as False, so a mail
outage can't break the request that triggered it.
"""
from __future__ import annotations

import html

import httpx

from app.config import get_settings


def enabled() -> bool:
    s = get_settings()
    return bool(s.mail_from and (s.brevo_api_key or s.resend_api_key))


def send(to: str, subject: str, text: str, html_body: str | None = None) -> bool:
    s = get_settings()
    if not enabled():
        return False
    try:
        if s.brevo_api_key:
            r = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": s.brevo_api_key, "accept": "application/json"},
                json={
                    "sender": {"name": s.mail_from_name, "email": s.mail_from},
                    "to": [{"email": to}],
                    "subject": subject,
                    "textContent": text,
                    **({"htmlContent": html_body} if html_body else {}),
                },
                timeout=15,
            )
        else:
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {s.resend_api_key}"},
                json={
                    "from": f"{s.mail_from_name} <{s.mail_from}>",
                    "to": [to],
                    "subject": subject,
                    "text": text,
                    **({"html": html_body} if html_body else {}),
                },
                timeout=15,
            )
        if r.status_code >= 300:
            print(f"[mail] provider rejected the message: HTTP {r.status_code} {r.text[:200]}")
            return False
        return True
    except httpx.HTTPError as exc:
        print(f"[mail] could not reach the email provider: {type(exc).__name__}")
        return False


def reset_email(name: str, link: str, minutes: int) -> tuple[str, str, str]:
    """(subject, plain text, html) for a password-reset message."""
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Reset your DevPilot password"
    text = (
        f"{greeting}\n\nSomeone asked to reset the password for your DevPilot account. "
        f"Open this link to choose a new one (it works once and expires in {minutes} minutes):\n\n"
        f"{link}\n\nIf you didn't ask for this, ignore this email. Your password stays the same."
    )
    safe_link = html.escape(link, quote=True)
    body = (
        f"<p>{html.escape(greeting)}</p>"
        "<p>Someone asked to reset the password for your DevPilot account.</p>"
        f'<p><a href="{safe_link}" style="display:inline-block;padding:10px 18px;'
        'background:#6d5ef5;color:#fff;border-radius:8px;text-decoration:none">'
        "Choose a new password</a></p>"
        f"<p>The link works once and expires in {minutes} minutes.</p>"
        "<p style=\"color:#666\">If you didn't ask for this, ignore this email. "
        "Your password stays the same.</p>"
    )
    return subject, text, body
