"""Alert dispatch: fire a webhook + optional email when a flagged event lands.

Called inline from the /api/events handler. Failures are logged to AlertLog
in SQLite — never raised, so a dead webhook doesn't disrupt event capture.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from . import db
from .settings import settings

log = logging.getLogger("gdlf.alerts")


async def _send_webhook(payload: dict) -> tuple[bool, str]:
    if not settings.alert_webhook_url:
        return False, "no webhook configured"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(settings.alert_webhook_url, json=payload)
        ok = 200 <= r.status_code < 300
        return ok, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _send_email_sync(subject: str, body: str) -> tuple[bool, str]:
    if not (settings.smtp_host and settings.smtp_from and settings.smtp_to):
        return False, "smtp not configured"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = settings.smtp_to
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass or "")
            s.send_message(msg)
        return True, "sent"
    except Exception as e:
        return False, str(e)


async def fire_for_event(event: db.Event) -> None:
    """Dispatch all alert channels for one flagged event.

    Idempotency: we log per-channel results in AlertLog; we don't dedupe
    aggressively — same flag firing twice is two alerts. Good enough for v1.
    """
    payload = {
        "kid": event.kid,
        "device": event.device,
        "host": event.host,
        "path": event.path,
        "query": event.query,
        "decision": event.decision,
        "rule": event.rule,
        "ts": event.ts.isoformat(),
    }
    summary = f"[gdlf] flagged: {event.kid or '?'} → {event.host}{event.path or ''}"

    ok_w, msg_w = await _send_webhook(payload)
    db.insert(db.AlertLog(kid=event.kid or "?", event_id=event.id, channel="webhook", ok=ok_w, detail=msg_w))

    # Email is sync; offload to a thread so we don't block the request.
    ok_e, msg_e = await asyncio.to_thread(
        _send_email_sync, summary, _format_email_body(event)
    )
    db.insert(db.AlertLog(kid=event.kid or "?", event_id=event.id, channel="email", ok=ok_e, detail=msg_e))


def _format_email_body(event: db.Event) -> str:
    return (
        f"Kid: {event.kid or '(unknown)'}\n"
        f"Device: {event.device or event.client_ip}\n"
        f"When: {event.ts.isoformat()} UTC\n"
        f"Decision: {event.decision}\n"
        f"Rule: {event.rule or '-'}\n"
        f"URL: https://{event.host}{event.path or ''}"
        f"{'?' + event.query if event.query else ''}\n"
    )
