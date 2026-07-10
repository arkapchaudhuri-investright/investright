"""Transactional email over plain SMTP — the one thing §10.6 said we couldn't do.

No new dependency: stdlib smtplib talks to any SMTP relay, so the same code runs
against a Gmail app password, Brevo, or anything else. Configure in .env:

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587           # 465 also works (implicit TLS)
    SMTP_USER=you@gmail.com
    SMTP_PASS=<16-char Google app password, NOT your account password>
    SMTP_FROM=InvestRight <you@gmail.com>    # optional, defaults to SMTP_USER

Leave those unset and enabled() is False: the password-reset routes 404 and the
UI keeps saying, honestly, that there is no self-serve reset. That's the same
degradation the Gemini key gets (§8.0) — a missing key never breaks a page.

Config is read at call time, not import time, so it doesn't matter whether
digest._load_env() has run yet when this module is first imported.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

TIMEOUT = 20


def _cfg():
    """The SMTP settings, or None if the relay isn't configured."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT") or 587),
        "user": user,
        "password": password,
        "sender": os.environ.get("SMTP_FROM") or user,
    }


def enabled():
    """Whether email can be sent at all. Gates the reset routes and the UI copy."""
    return _cfg() is not None


def send(to, subject, body, attachment=None):
    """Send one plain-text message. Returns True on success.

    `attachment` is an optional (filename, bytes) pair, sent as an opaque blob.
    Never raises: a dead relay must not 500 a page or, worse, tell the caller
    whether the address existed. Failures land in the gunicorn log.
    """
    cfg = _cfg()
    if not cfg:
        return False
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        name, blob = attachment
        msg.add_attachment(blob, maintype="application", subtype="octet-stream",
                           filename=name)
    try:
        ctx = ssl.create_default_context()
        if cfg["port"] == 465:                      # implicit TLS
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=TIMEOUT,
                                  context=ctx) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:                                       # STARTTLS (587)
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT) as s:
                s.starttls(context=ctx)
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True
    except Exception as exc:                        # noqa: BLE001 — see docstring
        print(f"[mailer] send to {to} failed: {exc.__class__.__name__}: {exc}",
              flush=True)
        return False
