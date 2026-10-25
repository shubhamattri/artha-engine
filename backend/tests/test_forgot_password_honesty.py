"""Forgot-password must not claim it sent something it cannot send.

With no SMTP host the endpoint used to mint a reset token, send nothing, log a warning nobody reads,
and still answer "a reset link has been sent" — so the screen reported success and the user waited
for mail that could never arrive. On this deployment smtp_host has never been set, so that was the
behaviour in production the whole time.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _app_client():
    from main import app
    return TestClient(app)


def test_no_smtp_configured_does_not_claim_a_link_was_sent(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "", raising=False)

    r = _app_client().post("/api/auth/forgot-password", json={"email": "someone@example.com"})
    assert r.status_code == 200
    msg = r.json()["message"].lower()
    assert "sent" not in msg or "can’t be sent" in msg or "can't be sent" in msg, msg
    assert "no email set up" in msg or "email set up" in msg, msg


def test_no_smtp_configured_mints_no_token(monkeypatch, db_session):
    """An undeliverable token is just a secret sitting in a log file."""
    from config import settings
    from models.user import PasswordResetToken
    monkeypatch.setattr(settings, "smtp_host", "", raising=False)

    before = db_session.query(PasswordResetToken).count()
    _app_client().post("/api/auth/forgot-password", json={"email": "someone@example.com"})
    assert db_session.query(PasswordResetToken).count() == before


def test_the_reply_is_identical_for_unknown_and_known_emails(monkeypatch):
    """The anti-enumeration guarantee must survive the honesty fix: whether mail is configured is a
    property of the DEPLOYMENT, so the message may state it, but it must not vary by account."""
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "", raising=False)
    c = _app_client()
    a = c.post("/api/auth/forgot-password", json={"email": "definitely-not-registered@example.com"})
    b = c.post("/api/auth/forgot-password", json={"email": "asha@example.com"})
    assert a.json() == b.json()


# --- setting a password from inside the app, no email involved -------------------
#
# client_for_user in conftest mounts only the profile router and takes (household, user), so these
# build their own client over the auth router with the signed-in user overridden.


def _auth_client(db_session, user):
    from fastapi import FastAPI

    from api import auth as auth_api
    from api.deps import get_current_user
    from database import get_db
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _seed_user(db_session, password="oldpassword1"):
    from auth.security import hash_password
    from models.tenant import Household
    from models.user import User
    hh = db_session.query(Household).first()
    if hh is None:
        hh = Household(name="H")
        db_session.add(hh); db_session.flush()
    u = User(household_id=hh.id, email="u@example.com", username="u", name="U",
             password_hash=hash_password(password), is_active=True)
    db_session.add(u); db_session.commit()
    return u


def test_change_password_requires_the_current_one(db_session):
    """A session cookie is not proof of the owner — a forgotten unlocked phone carries one. Without
    this check any open session could silently take the account over."""
    from auth.security import verify_password
    u = _seed_user(db_session)
    r = _auth_client(db_session, u).post(
        "/api/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "brandnewpass1"})
    assert r.status_code == 400, r.text
    db_session.refresh(u)
    assert verify_password("oldpassword1", u.password_hash), "password must be unchanged"


def test_change_password_sets_the_new_hash(db_session):
    from auth.security import verify_password
    u = _seed_user(db_session)
    r = _auth_client(db_session, u).post(
        "/api/auth/change-password",
        json={"current_password": "oldpassword1", "new_password": "brandnewpass1"})
    assert r.status_code == 200, r.text
    db_session.refresh(u)
    assert verify_password("brandnewpass1", u.password_hash)
    assert not verify_password("oldpassword1", u.password_hash)
    assert "brandnewpass1" not in u.password_hash, "stored as a hash, never as the password"


def test_change_password_burns_outstanding_reset_links(db_session):
    """An unused link minted before the change would otherwise still set the password afterwards."""
    from datetime import datetime, timedelta

    from models.user import PasswordResetToken
    u = _seed_user(db_session)
    db_session.add(PasswordResetToken(user_id=u.id, token_hash="a" * 64,
                                      expires_at=datetime.utcnow() + timedelta(minutes=30)))
    db_session.commit()
    _auth_client(db_session, u).post(
        "/api/auth/change-password",
        json={"current_password": "oldpassword1", "new_password": "brandnewpass1"})
    live = (db_session.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == u.id,
                    PasswordResetToken.used_at.is_(None)).count())
    assert live == 0


# --- SMTP From header ------------------------------------------------------------

def test_from_falls_back_to_the_authenticated_mailbox(monkeypatch):
    """smtp_from defaults to no-reply@artha.local, which no real provider will send as. Leaving that
    default while setting host/user/password fails with a 'not authenticated as' error that reads like
    a wrong password and is not one."""
    import auth.email as mailer
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "someone@gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "irrelevant", raising=False)
    monkeypatch.setattr(settings, "smtp_from", "Artha <no-reply@artha.local>", raising=False)

    sent = {}

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): sent["login"] = u
        def send_message(self, msg): sent["from"] = msg["From"]

    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    mailer.send_reset_email("dest@example.com", "https://example.test/reset?token=x")
    assert "someone@gmail.com" in sent["from"], sent
    assert "artha.local" not in sent["from"], sent


def test_an_explicitly_configured_from_is_respected(monkeypatch):
    """A real configured sender (a verified alias, say) must not be overwritten."""
    import auth.email as mailer
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "someone@gmail.com", raising=False)
    # A password is required for the config to count as usable now, so this test must supply one or
    # send_reset_email takes the not-configured path and never reaches SMTP at all.
    monkeypatch.setattr(settings, "smtp_password", "app-password", raising=False)
    monkeypatch.setattr(settings, "smtp_from", "Artha <money@myhousehold.com>", raising=False)

    sent = {}

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): sent["from"] = msg["From"]

    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    mailer.send_reset_email("dest@example.com", "https://example.test/reset?token=x")
    assert sent["from"] == "Artha <money@myhousehold.com>"


# --- a host alone is not a mail server -------------------------------------------

def test_host_without_credentials_is_not_treated_as_configured(monkeypatch):
    """Setting SMTP_HOST alone brought the lie straight back: enough to take the send path, nowhere
    near enough to send, because login() is only called when a user is present so Gmail refuses the
    session. The caller was told the mail was on its way."""
    from auth.email import smtp_is_configured
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "", raising=False)
    assert smtp_is_configured() is False

    r = _app_client().post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert "sent" not in r.json()["message"].lower() or "can’t be sent" in r.json()["message"]


def test_half_configured_credentials_are_not_enough(monkeypatch):
    from auth.email import smtp_is_configured
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "someone@gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "", raising=False)
    assert smtp_is_configured() is False


def test_a_complete_remote_config_is_configured(monkeypatch):
    from auth.email import smtp_is_configured
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "someone@gmail.com", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "app-password", raising=False)
    assert smtp_is_configured() is True


def test_a_loopback_relay_needs_no_credentials(monkeypatch):
    """A relay on localhost legitimately accepts unauthenticated mail; don't declare it unusable."""
    from auth.email import smtp_is_configured
    from config import settings
    monkeypatch.setattr(settings, "smtp_host", "localhost", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "", raising=False)
    assert smtp_is_configured() is True
