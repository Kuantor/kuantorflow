"""
Emails to the site's admin (#591): the one sender the admin notices share.

    notify.send_admin_email(notify.KIND_TEST, "KuantorFlow: subject", "body")

A new account (#592), the daily activity report (#593) and a learner nearing
a daily limit (#594) all send through `send_admin_email()`. It sends one
plain-text email over SMTP and promises three things:

* **Off unless configured.** With no `SMTP_HOST`, `SMTP_USER`,
  `SMTP_PASSWORD` and a recipient, nothing is sent and nothing breaks; local
  development needs no mail server. The settings are read **at call time**,
  from the environment `.env` fills, so nothing is cached at import.
* **Never raises, never waits long.** A notice is a convenience: no sign-in
  or save may fail because of one. Every error is caught and logged, and the
  connection has a `TIMEOUT` of a few seconds. It is sent inline rather than
  from a thread, because PythonAnywhere does not support threads in
  web apps.
* **One `NOTIFY` line per attempt**, in `logs/notify.log` (`applog.notify`):
  the kind, `sent` / `failed` / `skipped` / `dry-run`, the subject and the
  error -- **never the body, never the password**.

The settings (`.env.example` has the slots):

    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       587 (STARTTLS, the default) or 465 (SSL from the start)
    SMTP_USER       the sending account, also the From address
    SMTP_PASSWORD   for Gmail an **app password**, never the account's own
    NOTIFY_EMAIL    who receives them; default: the first ADMIN_EMAILS address
    NOTIFY_DRY_RUN  1 = log what would be sent, send nothing (needs no server)

`scripts/send_test_email.py` sends one test email with these settings.

Imports only `applog` from the app, so the data layer (`utils`, for #594) can
import it without a cycle.
"""

import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from dotenv import load_dotenv

import applog

# The web app has loaded `.env` by now (utils does it first); a console script
# that imports only this module has not. load_dotenv never overrides a value
# already set, so loading it twice is harmless.
load_dotenv(Path(__file__).with_name(".env"))

TIMEOUT = 5                 # seconds; a request waits this long at worst
DEFAULT_PORT = 587
SSL_PORT = 465
SENDER_NAME = "KuantorFlow"
MAX_ERROR_CHARS = 200
DEFAULT_LIMIT_PERCENT = 80  # #594; NOTIFY_LIMIT_PERCENT overrides, 0 = off

# The kinds of notice, written out like applog's USAGE_* constants: a typo in
# one would not fail, it would quietly start a new kind in the log.
KIND_TEST = "test"          # scripts/send_test_email.py
KIND_NEW_ACCOUNT = "new-account"    # app.auth_google_callback, #592
KIND_NEAR_LIMIT = "near-limit"      # utils.claim_pools, #594
KIND_DAILY = "daily"                # scripts/daily_report.py, #593

# The end of every notice about a learner (#592, #594): how to block them
# (#126), from the test repo's maintenance script. The address is the one
# thing to fill in, from `--list`, because the notices show it masked.
BLOCK_HINT = (
    "To block it, in a PythonAnywhere console:\n"
    "  cd ~/kuantorflow_automation\n"
    "  venv/bin/python maintenance/block_user.py --list\n"
    "  venv/bin/python maintenance/block_user.py <its address from --list> "
    "--reason \"...\"\n")


def _setting(name):
    return os.environ.get(name, "").strip()


def recipient():
    """`NOTIFY_EMAIL`, or the first address in `ADMIN_EMAILS`, or None."""
    explicit = _setting("NOTIFY_EMAIL")
    if explicit:
        return explicit
    admins = [a.strip() for a in _setting("ADMIN_EMAILS").split(",") if a.strip()]
    return admins[0] if admins else None


def admin_addresses(raw=None):
    """`ADMIN_EMAILS`, read as `web._admin_emails()` reads it: lowercased,
    blanks dropped. Here as well because `utils` cannot import `web` (#594);
    a test holds the two to the same answers."""
    if raw is None:
        raw = os.environ.get("ADMIN_EMAILS", "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def limit_percent():
    """`NOTIFY_LIMIT_PERCENT` (#594): default 80, at most 100, 0 = off. A value
    that is not a whole number keeps the default rather than silencing it."""
    raw = _setting("NOTIFY_LIMIT_PERCENT")
    try:
        return min(int(raw), 100) if raw else DEFAULT_LIMIT_PERCENT
    except ValueError:
        return DEFAULT_LIMIT_PERCENT


def limit_threshold(limit):
    """The count at which a learner is "near" a daily `limit` (#594), or None
    when the warning is off.

    `ceil(percent x limit)`, from the limit the claim was given, so a ceiling
    changed in `.env` moves its warning with it.
    """
    percent = limit_percent()
    if percent <= 0 or not limit or limit <= 0:
        return None
    return -(-limit * percent // 100)


def mask_email(email):
    """An address shortened for a notice (#592): `an***@gmail.com`.

    The notices name a learner by account number and this, never the full
    address, as Anton asked. At most two letters of the local part are kept,
    and always fewer than it has, so a one- or two-letter name still hides
    something; the domain is kept whole. Not an address at all: `***`.
    """
    local, at, domain = (email or "").strip().lower().partition("@")
    if not at or not local or not domain:
        return "***"
    return local[:min(2, len(local) - 1)] + "***@" + domain


def _dry_run():
    """`web._bool_env`'s reading of a yes/no setting, default off."""
    return _setting("NOTIFY_DRY_RUN").lower() not in ("0", "false", "no", "off", "")


def _one_line(text):
    """A subject on one line: a line break in a header would start another."""
    return " ".join(str(text).split())


def _error_text(exc, password):
    """The error for the log: its type and message, on one line, cut short,
    with the password blanked in case the server's reply repeats it."""
    text = _one_line(f"{type(exc).__name__}: {exc}")
    for secret in {password, password.replace(" ", "")}:
        if secret:
            text = text.replace(secret, "***")
    return text[:MAX_ERROR_CHARS]


def _message(sender, to, subject, body, kind):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, sender))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(
        f"{body}\n\n--\nKuantorFlow admin notice ({kind}). "
        "Sent because SMTP_HOST is set in the site's .env; clear it to stop.\n")
    return msg


def _connect(host, port):
    context = ssl.create_default_context()
    if port == SSL_PORT:
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        server.starttls(context=context)
    except Exception:
        _close(server)
        raise
    return server


def _close(server):
    try:
        server.quit()
    except Exception:       # noqa: BLE001 - a connection already gone
        try:
            server.close()
        except Exception:   # noqa: BLE001
            pass


def send_admin_email(kind, subject, body):
    """Email the admin; return what happened: `sent`, `failed`, `skipped`
    (not configured) or `dry-run`. Never raises -- the caller carries on
    whatever the answer, and most callers ignore it."""
    started = time.monotonic()
    try:
        subject = _one_line(subject)
        body = str(body)
        if _dry_run():
            applog.notify(kind, "dry-run", subject, chars=len(body))
            return "dry-run"

        host, user, password = (_setting("SMTP_HOST"), _setting("SMTP_USER"),
                                os.environ.get("SMTP_PASSWORD", ""))
        to = recipient()
        missing = [name for name, value in (
            ("SMTP_HOST", host), ("SMTP_USER", user),
            ("SMTP_PASSWORD", password), ("NOTIFY_EMAIL", to)) if not value]
        if missing:
            applog.notify(kind, "skipped", subject, missing=",".join(missing))
            return "skipped"
    except Exception as exc:    # noqa: BLE001 - never in the caller's way
        applog.notify(kind, "failed", None, error=_error_text(exc, ""))
        return "failed"

    try:
        port = int(_setting("SMTP_PORT") or DEFAULT_PORT)
        msg = _message(user, to, subject, body, kind)
        server = _connect(host, port)
        try:
            server.login(user, password)
            server.send_message(msg)
        finally:
            _close(server)
    except Exception as exc:    # noqa: BLE001 - never in the caller's way
        applog.notify(kind, "failed", subject,
                      ms=round((time.monotonic() - started) * 1000),
                      error=_error_text(exc, password))
        return "failed"

    applog.notify(kind, "sent", subject,
                  ms=round((time.monotonic() - started) * 1000))
    return "sent"
