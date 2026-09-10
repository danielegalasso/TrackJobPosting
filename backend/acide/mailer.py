"""SMTP delivery for alert digests.

The digest is rendered as the same card the portal shows, so a subscriber
recognises what they are looking at. Every message carries a plain-text
alternative and an unsubscribe pointer, because an alert nobody can turn off
is spam.
"""

from __future__ import annotations

import contextlib
import html
import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .models import EmailConfig, Job

BRAND = "#0e744e"
ACCENT = "#f59e0b"


class MailError(RuntimeError):
    """Delivery failed; the caller decides whether to retry."""


def _connect(config: EmailConfig) -> smtplib.SMTP | smtplib.SMTP_SSL:
    if not config.smtp_server:
        raise MailError("no SMTP server configured")
    try:
        if config.smtp_port == 465 and not config.use_tls:
            return smtplib.SMTP_SSL(config.smtp_server, config.smtp_port, timeout=30)
        server = smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=30)
        server.ehlo()
        if config.use_tls:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        return server
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"could not connect to {config.smtp_server}: {exc}") from exc


def send(config: EmailConfig, to_address: str, subject: str, html_body: str, text_body: str) -> None:
    """Deliver one message. Raises `MailError` on any failure."""
    if not config.enabled:
        raise MailError("email delivery is disabled in setup.json")
    if not config.sender_email:
        raise MailError("no sender address configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config.sender_name, config.sender_email))
    message["To"] = to_address
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=config.sender_email.split("@")[-1])
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    server = _connect(config)
    try:
        if config.sender_password:
            server.login(config.sender_email, config.sender_password)
        server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "SMTP authentication failed — for Gmail use an app password, not the account password"
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"SMTP delivery failed: {exc}") from exc
    finally:
        with contextlib.suppress(OSError, smtplib.SMTPException):
            server.quit()


def handshake(config: EmailConfig) -> str:
    """Open a connection and authenticate without sending anything."""
    server = _connect(config)
    try:
        if config.sender_password:
            server.login(config.sender_email, config.sender_password)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "SMTP authentication failed — for Gmail use an app password, not the account password"
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"SMTP handshake failed: {exc}") from exc
    finally:
        with contextlib.suppress(OSError, smtplib.SMTPException):
            server.quit()
    return f"Connected and authenticated to {config.smtp_server}:{config.smtp_port}."


def _money(job: Job) -> str:
    if not job.amount:
        return "Compensation not published"
    return f"{job.currency} {job.amount:,.0f} · {job.rate}"


def _card(job: Job, portal_url: str) -> str:
    badge_colour = ACCENT if job.category_type.startswith("Pivot") else BRAND
    return f"""
      <tr><td style="padding:0 0 14px 0;">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="border:1px solid #e2e8f0;border-radius:8px;background:#ffffff;">
          <tr><td style="padding:16px 18px;">
            <div style="font:600 16px/1.35 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111827;">
              <a href="{html.escape(job.apply_url)}" style="color:#111827;text-decoration:none;">{html.escape(job.title)}</a>
            </div>
            <div style="font:500 14px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#374151;">
              {html.escape(job.company)}
            </div>
            <div style="font:400 13px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#6b7280;">
              {html.escape(job.location)} · {html.escape(_money(job))}
            </div>
            <div style="margin:10px 0 8px 0;">
              <span style="display:inline-block;padding:3px 8px;border-radius:4px;background:#ecfdf5;color:#047857;font:600 11px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">Experience {job.experience_fit_score}%</span>
              <span style="display:inline-block;padding:3px 8px;border-radius:4px;background:#eff6ff;color:#1d4ed8;font:600 11px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">Pivot {job.interest_fit_score}%</span>
              <span style="display:inline-block;padding:3px 8px;border-radius:4px;background:#f8fafc;color:{badge_colour};font:600 11px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">{html.escape(job.category_type)}</span>
            </div>
            <div style="font:400 13px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#4b5563;">
              {html.escape(job.alert_summary)}
            </div>
            <div style="margin-top:12px;">
              <a href="{html.escape(job.apply_url)}"
                 style="display:inline-block;padding:8px 14px;border-radius:5px;background:{BRAND};color:#ffffff;font:600 13px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;text-decoration:none;">View posting</a>
              <a href="{html.escape(portal_url)}"
                 style="margin-left:10px;font:500 13px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#6b7280;text-decoration:none;">Open portal</a>
            </div>
          </td></tr>
        </table>
      </td></tr>"""


def render_digest(
    jobs: Sequence[Job], portal_url: str, unsubscribe_url: str
) -> tuple[str, str, str]:
    """Return `(subject, html_body, text_body)` for a batch of matches."""
    count = len(jobs)
    lead = jobs[0]
    subject = (
        f"ACIDE-Watch: {lead.title} at {lead.company}"
        if count == 1
        else f"ACIDE-Watch: {count} new roles match your alert"
    )

    cards = "".join(_card(job, portal_url) for job in jobs)
    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f8fafc;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f8fafc;padding:24px 12px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:620px;">
        <tr><td style="background:{BRAND};border-radius:8px 8px 0 0;padding:16px 18px;">
          <span style="font:700 18px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#ffffff;">ACIDE-Watch</span>
          <span style="font:400 13px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#a7f3d0;margin-left:8px;">career intelligence digest</span>
        </td></tr>
        <tr><td style="background:#ffffff;padding:18px;border:1px solid #e2e8f0;border-top:0;">
          <p style="font:400 14px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#374151;margin:0 0 16px 0;">
            {count} new posting{"s" if count != 1 else ""} cleared your fit thresholds since the last digest.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">{cards}</table>
        </td></tr>
        <tr><td style="padding:14px 18px;background:#f1f5f9;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 8px 8px;">
          <p style="font:400 11px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#64748b;margin:0;">
            You are receiving this because you created an alert on a self-hosted ACIDE-Watch instance.
            Listings are aggregated from public employer career feeds; ACIDE-Watch is not an employer
            or recruiter and does not guarantee a posting's accuracy or availability.
            <a href="{html.escape(unsubscribe_url)}" style="color:{BRAND};">Unsubscribe</a>.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    lines = [f"{count} new posting(s) matched your ACIDE-Watch alert.", ""]
    for job in jobs:
        lines += [
            f"* {job.title} — {job.company}",
            f"  {job.location} · {_money(job)}",
            f"  Experience {job.experience_fit_score}% | Pivot {job.interest_fit_score}% "
            f"| {job.category_type}",
            f"  {job.alert_summary}",
            f"  {job.apply_url}",
            "",
        ]
    lines += [f"Portal: {portal_url}", f"Unsubscribe: {unsubscribe_url}"]
    return subject, html_body, "\n".join(lines)
