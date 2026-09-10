"""`/api/alerts` — email alert subscriptions and their lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from .. import alerts as alerts_service
from .. import config as config_module
from .. import db, mailer
from ..models import AlertCreate, AlertSubscription, HandshakeResult

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("", response_model=AlertSubscription, status_code=201)
def create_alert(payload: AlertCreate) -> AlertSubscription:
    """Capture the current filter state as a recurring email alert."""
    return db.create_alert(str(payload.email), payload.filters)


@router.get("", response_model=list[AlertSubscription])
def list_alerts(
    email: str | None = Query(None, description="Restrict to one address"),
    active_only: bool = False,
) -> list[AlertSubscription]:
    return db.list_alerts(email=email, active_only=active_only)


@router.post("/{alert_id}/pause", response_model=AlertSubscription)
def pause_alert(alert_id: int) -> AlertSubscription:
    subscription = db.set_alert_active(alert_id, False)
    if subscription is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return subscription


@router.post("/{alert_id}/resume", response_model=AlertSubscription)
def resume_alert(alert_id: int) -> AlertSubscription:
    subscription = db.set_alert_active(alert_id, True)
    if subscription is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return subscription


@router.delete("/{alert_id}", status_code=204, response_class=Response)
def delete_alert(alert_id: int) -> Response:
    if not db.delete_alert(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return Response(status_code=204)


@router.get("/{alert_id}/unsubscribe", response_class=HTMLResponse)
def unsubscribe(alert_id: int) -> HTMLResponse:
    """One-click unsubscribe target linked from every digest footer."""
    removed = db.delete_alert(alert_id)
    message = (
        "You have been unsubscribed. No further digests will be sent to this alert."
        if removed
        else "That alert no longer exists — nothing further will be sent."
    )
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
        <title>ACIDE-Watch — Unsubscribed</title>
        <meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
                     background:#f8fafc;color:#111827;display:flex;min-height:100vh;
                     align-items:center;justify-content:center;padding:24px;">
          <div style="max-width:460px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:28px;">
            <div style="font-weight:700;color:#0e744e;font-size:20px;margin-bottom:8px;">ACIDE-Watch</div>
            <p style="margin:0;color:#374151;">{message}</p>
          </div>
        </body></html>"""
    )


@router.delete("/by-email/{email}", response_model=dict)
def delete_by_email(email: str) -> dict:
    """Erasure request: remove every subscription for an address."""
    return {"deleted": db.delete_alerts_for_email(email)}


@router.post("/{alert_id}/send-now", response_model=HandshakeResult)
def send_now(alert_id: int) -> HandshakeResult:
    """Force a digest for one subscription, for testing SMTP end to end."""
    subscriptions = [item for item in db.list_alerts() if item.id == alert_id]
    if not subscriptions:
        raise HTTPException(status_code=404, detail="alert not found")
    config = config_module.load()
    try:
        count = alerts_service.dispatch_for(subscriptions[0], config)
    except mailer.MailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    detail = (
        f"Sent {count} match(es)." if count else "No undispatched matches for this alert."
    )
    return HandshakeResult(ok=True, detail=detail)
