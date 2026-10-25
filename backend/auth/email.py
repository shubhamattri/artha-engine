"""Password-reset email delivery.

If SMTP is configured (env), send a real email. Otherwise (local dev) write the
reset link to a log file and stdout so the flow is testable without a mail
server. The raw token is NEVER returned through the API.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from config import settings

log = logging.getLogger("artha.auth")
_DEV_LOG = "/tmp/artha_reset_links.log"


def smtp_is_configured() -> bool:
    """Can this deployment actually deliver mail — not merely, does it have a host.

    Setting SMTP_HOST alone made forgot-password answer "a reset link has been sent" again, because a
    host is enough to take the send path and not nearly enough to send: the code only calls login()
    when a user is present, so Gmail refused the unauthenticated session, the exception was logged,
    and the caller was told the mail was on its way. A half-configured server is indistinguishable
    from a working one at the point where we choose what to tell someone.

    A relay on the loopback interface legitimately needs no credentials, so that stays allowed.
    Anything remote must have both, because a remote relay that accepts unauthenticated mail from us
    is either misconfigured or an open relay.
    """
    if not settings.smtp_host:
        return False
    if settings.smtp_host in {"localhost", "127.0.0.1", "::1"}:
        return True
    return bool(settings.smtp_user and settings.smtp_password)


def send_reset_email(to_email: str, reset_url: str) -> None:
    subject = "Reset your Artha password"
    body = (
        f"Someone requested a password reset for your Artha account.\n\n"
        f"Reset it here (valid {settings.reset_token_ttl_minutes} minutes):\n"
        f"{reset_url}\n\n"
        f"If this wasn't you, ignore this email — your password stays unchanged."
    )

    if not smtp_is_configured():
        # Dev fallback — no usable mail server configured.
        line = f"[PASSWORD RESET] {to_email} -> {reset_url}"
        log.warning(line)
        try:
            with open(_DEV_LOG, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        return

    # Gmail — and most providers — reject or silently rewrite a From that is not the authenticated
    # mailbox. smtp_from defaults to the unroutable placeholder "no-reply@artha.local", so configuring
    # host/user/password and leaving that default would fail with a "not authenticated as" error that
    # reads like a credentials problem and is not one. Send as the account that logged in.
    sender = settings.smtp_from
    if settings.smtp_user and "artha.local" in sender:
        sender = f"Artha <{settings.smtp_user}>"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
