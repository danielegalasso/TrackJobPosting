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


GMAIL_HINT = (
    "For Gmail: turn on 2-Step Verification, create a 16-character app password "
    "at myaccount.google.com/apppasswords, and set the sender address to that "
    "same Gmail account. Your normal account password will always be rejected."
)


def _quietly_close(server: smtplib.SMTP | smtplib.SMTP_SSL) -> None:
    with contextlib.suppress(OSError, smtplib.SMTPException):
        server.quit()


def _connect(config: EmailConfig) -> smtplib.SMTP | smtplib.SMTP_SSL:
    """Open a connection, choosing implicit or negotiated TLS by port.

    Port 465 is implicit TLS: the handshake happens before any SMTP verb, so
    it must never be spoken to in plaintext, whatever the `use_tls` box says.
    Treating that box as authoritative on 465 produced a plaintext socket
    against a TLS-only port, which hangs until the timeout.
    """
    host = config.smtp_server.strip()
    if not host:
        raise MailError("no SMTP server configured")

    try:
        if config.smtp_port == 465:
            context = ssl.create_default_context()
            return smtplib.SMTP_SSL(host, config.smtp_port, timeout=30, context=context)

        server = smtplib.SMTP(host, config.smtp_port, timeout=30)
        server.ehlo()
        if config.use_tls:
            try:
                server.starttls(context=ssl.create_default_context())
            except smtplib.SMTPNotSupportedError as exc:
                _quietly_close(server)
                raise MailError(
                    f"{host}:{config.smtp_port} does not offer STARTTLS — "
                    "use port 465 for implicit TLS, or untick TLS for a plaintext relay"
                ) from exc
            server.ehlo()
        return server
    except MailError:
        raise
    except ssl.SSLError as exc:
        raise MailError(
            f"TLS handshake with {host}:{config.smtp_port} failed: {exc}. "
            "Port 587 expects STARTTLS (TLS ticked); port 465 expects implicit TLS."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"could not connect to {host}:{config.smtp_port}: {exc}") from exc


def _auth_error(exc: smtplib.SMTPAuthenticationError, *, retried: bool) -> MailError:
    """Surface the server's own refusal rather than a generic sentence."""
    detail = getattr(exc, "smtp_error", b"") or b""
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")
    code = getattr(exc, "smtp_code", "") or ""
    reason = " ".join(str(detail).split())[:300]
    note = " (also retried without the spaces)" if retried else ""
    return MailError(f"SMTP authentication failed ({code}){note}: {reason} — {GMAIL_HINT}")


def _authenticate(config: EmailConfig) -> smtplib.SMTP | smtplib.SMTP_SSL:
    """Connect and log in, returning a live authenticated server."""
    password = config.sender_password
    attempts = [password]
    compact = "".join(password.split())
    if compact and compact != password:
        # Google shows app passwords as "abcd efgh ijkl mnop". The spaces are
        # display formatting and the server rejects them, so a pasted password
        # fails for a reason nothing in the UI explains. A genuine passphrase
        # containing spaces still wins, because it is tried first.
        attempts.append(compact)

    failure: MailError | None = None
    for index, candidate in enumerate(attempts):
        # A fresh connection per attempt: some servers drop the session after
        # a failed AUTH, and reusing that socket reports a misleading error.
        server = _connect(config)
        if not candidate:
            return server
        try:
            server.login(config.sender_email.strip(), candidate)
            return server
        except smtplib.SMTPAuthenticationError as exc:
            _quietly_close(server)
            failure = _auth_error(exc, retried=index > 0)
        except (OSError, smtplib.SMTPException) as exc:
            _quietly_close(server)
            raise MailError(f"SMTP login failed: {exc}") from exc

    raise failure or MailError("SMTP authentication failed")


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

    server = _authenticate(config)
    try:
        server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"SMTP delivery failed: {exc}") from exc
    finally:
        _quietly_close(server)


def handshake(config: EmailConfig) -> str:
    """Open a connection and authenticate without sending anything."""
    if not config.sender_email.strip():
        raise MailError("no sender address configured")
    server = _authenticate(config)
    _quietly_close(server)

    mode = "implicit TLS" if config.smtp_port == 465 else ("STARTTLS" if config.use_tls else "plaintext")
    who = f" as {config.sender_email.strip()}" if config.sender_password else " (no authentication)"
    return f"Connected to {config.smtp_server.strip()}:{config.smtp_port} over {mode}{who}."


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
