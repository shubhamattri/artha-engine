"""Gmail auto-pull (READ-ONLY) — fetch statement attachments so they don't need manual upload.

Scope is gmail.readonly: can search + read, can never send/delete/modify. OAuth client + token
live under secrets/ (gitignored). Authorize once via scripts/gmail_auth.py. The fetched
attachments are handed to services.ingest.ingest_statement (auto-detect + unlock + parse + dedup),
so re-syncing is safe.
"""

from __future__ import annotations

import base64
import os

from config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_EXTS = (".pdf", ".csv", ".xls", ".xlsx")


def is_connected() -> bool:
    return os.path.exists(settings.gmail_token_path)


def _service():
    if not is_connected():
        return None
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = Credentials.from_authorized_user_file(settings.gmail_token_path, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(settings.gmail_token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _iter_parts(payload: dict):
    """Walk a Gmail message payload tree yielding every part."""
    stack = [payload]
    while stack:
        p = stack.pop()
        yield p
        stack.extend(p.get("parts", []) or [])


def _message_text(payload: dict) -> str:
    """Best-effort plain-text body of a Gmail message (falls back to stripped HTML)."""
    import re

    text, html = "", ""
    for p in _iter_parts(payload):
        mime = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if not data:
            continue
        try:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        if mime == "text/plain":
            text += decoded + "\n"
        elif mime == "text/html":
            html += decoded
    if text.strip():
        return text
    return re.sub(r"<[^>]+>", " ", html)  # crude HTML→text


# Bank transaction-alert senders (per-spend emails — the daily-capture source, NOT statements).
ALERT_QUERY = (
    "newer_than:{days}d -has:attachment ("
    "from:(alerts@hdfcbank.net OR alerts@axisbank.com OR cards@axisbank.com OR "
    "credit_cards@icicibank.com OR alerts@icicibank.com OR alerts@kotak.com OR "
    "estatements@sbicard.com OR alerts@sbicard.com) "
    'OR subject:(spent OR debited OR "UPI txn" OR "has been debited" OR "payment was made"))'
)


def fetch_transaction_alerts(days: int = 14, max_messages: int = 60,
                             known_fps: set[str] | None = None,
                             fp=None) -> list[dict]:
    """Return per-transaction bank alert emails as [{id, date, sender, subject, body}] for parsing.
    Captures spend at the PAYMENT layer (the bank), so it covers every merchant — Blinkit included —
    without needing any merchant integration.

    `known_fps` are fingerprints the caller has already ingested, and `fp` maps a message id to one.
    Bodies are fetched with ONE HTTP round trip per message, so re-downloading everything on every
    sync cost ~10s for mail that was already imported — the single largest chunk of the app-open sync.
    The listing is cheap and returns ids, so anything already known is skipped before its body is ever
    requested. Without both arguments the behaviour is unchanged (fetch everything).
    """
    svc = _service()
    if svc is None:
        return []
    q = ALERT_QUERY.format(days=days)
    listing = svc.users().messages().list(userId="me", q=q, maxResults=max_messages).execute()
    out: list[dict] = []
    skip = known_fps or set()
    for ref in listing.get("messages", []):
        if skip and fp is not None and fp(ref["id"]) in skip:
            continue
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        h = {x["name"]: x["value"] for x in msg.get("payload", {}).get("headers", [])}
        out.append({
            "id": ref["id"],
            "date": h.get("Date", ""),
            "sender": h.get("From", ""),
            "subject": h.get("Subject", ""),
            "snippet": msg.get("snippet", ""),  # Gmail's clean pre-extract — usually has merchant + amount
            "body": _message_text(msg.get("payload", {}))[:1500],
        })
    return out


def fetch_statement_attachments(query: str | None = None, max_messages: int = 30,
                                with_context: bool = False):
    """Statement attachments from the mailbox.

    with_context=True yields (filename, bytes, meta) where meta carries the sender, subject and any
    password the covering email states. Banks put the key in the same message as the lock, so
    returning only (filename, bytes) left real statements unopenable while the app asked the user to
    configure passwords the email had already supplied.

    Default stays (filename, bytes) so existing callers are unchanged.
    """
    from services.statement_email import card_issuer, passwords_from_email

    svc = _service()
    q = query or settings.gmail_query
    res = svc.users().messages().list(userId="me", q=q, maxResults=max_messages).execute()
    out = []
    for m in res.get("messages", []):
        msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        payload = msg.get("payload", {})
        h = {x["name"]: x["value"] for x in payload.get("headers", [])}
        meta = None
        if with_context:
            issuer = card_issuer(h.get("From", ""))
            meta = {
                "sender": h.get("From", ""),
                "subject": h.get("Subject", ""),
                "bank": issuer[0] if issuer else None,
                "card_type": issuer[1] if issuer else None,
                # Never logged, never returned in an API response — handed straight to the opener.
                "passwords": passwords_from_email(_message_text(payload)),
            }
        for part in _iter_parts(payload):
            fn = part.get("filename") or ""
            if not fn.lower().endswith((".pdf", ".csv", ".xls", ".xlsx")):
                continue
            body = part.get("body") or {}
            aid = body.get("attachmentId")
            if not aid:
                continue
            att = svc.users().messages().attachments().get(
                userId="me", messageId=m["id"], id=aid).execute()
            data = base64.urlsafe_b64decode(att["data"])
            out.append((fn, data, meta) if with_context else (fn, data))
    return out