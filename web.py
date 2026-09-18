"""The Flask application object and the configuration it is built with.

Split out of `app.py` under #418, and it is the piece that has to exist
first: every feature module the ticket describes needs `app` to decorate
with, and `app.py` cannot hand it over while it is also the module they
are imported *from*. This is the one place the object is created, so a
route module imports it from here and nothing imports `app.py`.

**Only the boot block lives here so far** -- the object, its secret and
session lifetime, the environment readers and the values they produce.
The identity and permission helpers belong here too and follow in their
own change, because moving them at the same time would mix two things
that fail in different ways.

Nothing here reads a name that `app.py` defines, which is what keeps the
import one-directional. And nothing that *uses* these values moved, so
every test that patches `app.ACCESS_KEYWORD` or `app.ADMIN_EMAILS` still
reaches the code it means to: `app.py` binds them into its own namespace
and its own routes read them from there.
"""

import os
import uuid
from datetime import timedelta
from pathlib import Path

from flask import Flask
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix


app = Flask(__name__)
# Needed for flash messages (session cookie). Override in production:
# set the SECRET_KEY environment variable on PythonAnywhere.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# How long a signed-in session survives once marked permanent (see the OAuth
# callback). Keeps the visitor greeted by name across browser restarts without
# any server-side storage — the identity still lives only in the signed cookie.
app.permanent_session_lifetime = timedelta(days=30)


def _bool_env(name, default):
    """A yes/no setting from the environment; unset keeps `default`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# The session cookie is this app's entire authentication state: the keyword gate
# pass and, since #40, the signed-in Google identity that #158's admin check and
# #127's ownership filter both read. Flask leaves every protective flag at its
# default, which means Secure is **off** — so that identity would travel in
# clear text if a request ever arrived over plain HTTP (#274). HttpOnly is
# Flask's default already and is stated here so all three are one decision in
# one place rather than two decisions and an inheritance.
#
# SameSite is **Lax and must not be tightened to Strict**. Authlib keeps the
# OAuth state and nonce in this same session, and Google's callback arrives as a
# top-level GET navigation *from accounts.google.com* — a cross-site request.
# Strict withholds the cookie on exactly that navigation, so the state is gone
# when Authlib checks it, authorize_access_token() raises, and sign-in fails
# with nothing to see but a silent redirect back to the index.
#
# Secure defaults to **on**, which is what the WSGI process on PythonAnywhere
# gets. `python app.py` turns it off at the bottom of this file, because local
# development is http://localhost and a browser accepts no Secure cookie there:
# the gate would set its pass, fail to store it, and ask for the keyword again
# forever. The default is this way round deliberately — a forgotten override
# then breaks locally and visibly, rather than quietly unprotecting the cookie
# on the deployed site, where nothing would look wrong.
app.config.update(
    SESSION_COOKIE_SECURE=_bool_env("SESSION_COOKIE_SECURE", True),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# PythonAnywhere serves the app behind a proxy; trust its X-Forwarded-*
# headers so absolute URLs (og:image etc.) use https and the real host.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Keyword that gates access to the whole site (set ACCESS_KEYWORD in .env).
ACCESS_KEYWORD = os.environ.get("ACCESS_KEYWORD", "password")


def _admin_emails(raw=None):
    """The configured administrator addresses, lowercased (issue #158).

    In `.env` rather than a column on `users`, deliberately: the admin's job is
    to block other accounts, so admin-ness must not live in the table that the
    blocking flow edits — nor be reachable by a stray UPDATE or a restored
    backup. It also sidesteps the bootstrap problem, since the first admin has
    no row until they sign in.

    Unset, empty, or all-blank means the site simply has no admin.
    """
    if raw is None:
        raw = os.environ.get("ADMIN_EMAILS", "")
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


ADMIN_EMAILS = _admin_emails()

# --- Optional "Sign in with Google" (OAuth 2.0 / OpenID Connect) -------------
# Purely optional: a visitor can sign in to be greeted by name, or stay
# anonymous ("invisible") and use the site exactly as before. Enabled only when
# both credentials are set (see .env.example) — otherwise it disables itself and
# no sign-in button is shown. Since #148 a signed-in identity is recorded in the
# `users` table (email, name, Google's subject id) so that cards, settings and
# chats can belong to someone; anonymous visitors are still never written down.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTH_AVAILABLE = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

def _int_env(name, default):
    """A whole-number setting from the environment; anything unparseable falls
    back to the default rather than taking the app down at import time."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        app.logger.warning("%s is not a number; using %s", name, default)
        return default


oauth = OAuth(app)
if GOOGLE_AUTH_AVAILABLE:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# Reject oversized Mykola chat payloads before they reach the model — guards
# against memory blowups and runaway Anthropic API costs. Scoped to the chat
# endpoint rather than a global MAX_CONTENT_LENGTH, so it can't clip legitimate
# (and much larger) .mht uploads on the index route. 1 MB is generous for text.
MAX_MYKOLA_REQUEST_BYTES = 1024 * 1024

# How much of Mykola an anonymous visitor gets before signing in (issue #164).
# Every message costs Anthropic credits, and only /mykola/chat can reach the
# model without an account — the recap endpoints return early without one.
#   ANONYMOUS_MESSAGE_LIMIT — per browser session. A nudge, not a spend cap:
#     anonymous sessions are not permanent, so closing the browser or clearing
#     cookies resets it. It is what people actually meet.
#   ANONYMOUS_DAILY_LIMIT — every anonymous message, everyone, per day. This
#     is the one that bounds the bill, counted in the database so it holds
#     across worker processes.
# 0 (or unset) disables either limit. Signed-in users are never limited.
ANONYMOUS_MESSAGE_LIMIT = _int_env("ANONYMOUS_MESSAGE_LIMIT", 10)
ANONYMOUS_DAILY_LIMIT = _int_env("ANONYMOUS_DAILY_LIMIT", 200)

LOG_DIR = Path(__file__).parent / "mykola_logs"
LOG_DIR.mkdir(exist_ok=True)
APP_BOOT_ID = uuid.uuid4().hex
