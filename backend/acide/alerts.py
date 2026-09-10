"""Alert dispatch: match stored jobs against subscriptions and email them.

Deduplication lives in the `alert_dispatches` ledger, so a subscriber is
never sent the same posting twice even if the inspector re-scores it or the
daemon runs more often than expected.
"""

from __future__ import annotations

import os

from . import config as config_module
from . import db, mailer
from .logbus import bus
from .models import AlertSubscription, SetupConfig

MAX_JOBS_PER_DIGEST = 25


def portal_url() -> str:
    return os.environ.get("ACIDE_PUBLIC_URL", "http://localhost:8000").rstrip("/")


def unsubscribe_url(subscription: AlertSubscription) -> str:
    return f"{portal_url()}/api/alerts/{subscription.id}/unsubscribe"


def dispatch_for(subscription: AlertSubscription, config: SetupConfig) -> int:
    """Send one subscriber their outstanding matches. Returns jobs emailed."""
    jobs = db.undispatched_jobs(subscription, limit=MAX_JOBS_PER_DIGEST)
    # Only surface postings that actually cleared a fit threshold; the raw
    # filter set alone would email every indexed role.
    jobs = [
        job
        for job in jobs
        if job.experience_fit_score >= config.scoring.experience_threshold
        or job.interest_fit_score >= config.scoring.interest_threshold
    ]
    if not jobs:
        return 0

    subject, html_body, text_body = mailer.render_digest(
        jobs, portal_url(), unsubscribe_url(subscription)
    )
    mailer.send(config.email, subscription.email, subject, html_body, text_body)
    db.record_dispatch(subscription.id, [job.id for job in jobs])
    return len(jobs)


def dispatch_pending(config: SetupConfig | None = None) -> int:
    """Run every active subscription. Returns the number of emails sent."""
    config = config or config_module.load()
    if not config.email.enabled:
        bus.publish("Email delivery disabled; alert digests were not sent.", "warn")
        return 0

    sent = 0
    for subscription in db.list_alerts(active_only=True):
        try:
            count = dispatch_for(subscription, config)
        except mailer.MailError as exc:
            bus.publish(f"Alert to {subscription.email} failed: {exc}", "error")
            continue
        if count:
            sent += 1
            bus.publish(f"Emailed {count} match(es) to {subscription.email}.", "success")
    return sent
