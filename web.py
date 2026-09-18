"""The Flask application object and the configuration it is built with.

Split out of `app.py` under #418, and it is the piece that has to exist
first: every feature module the ticket describes needs `app` to decorate
with, and `app.py` cannot hand it over while it is also the module they
are imported *from*. This is the one place the object is created, so a
route module imports it from here and nothing imports `app.py`.

Two things live here. The **boot block** -- the object, its secret and
session lifetime, the environment readers and the values they produce --
and, since step four, the **identity and permission helpers**: who is
asking, whether they are blocked, an admin, or the author of a card, and
what they are told when the answer is no.

They are together because they are the same dependency. A feature module
needs `app` to decorate with and `is_admin()` to ask, and neither may
come from `app.py` without the route table coming too.

Nothing here reads a name that `app.py` defines, which is what keeps the
import one-directional, and every function here is reached through this
module rather than through a copy bound at import -- so one patch on
`web.current_settings` covers every caller in every module, including
the ones #418 has not written yet.
"""

import hashlib
import os
import uuid
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, session
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix

import settings_store
import utils


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


# --- who is asking, and what they may do ------------------------------------
# The identity and permission helpers (#418, step four). They are here rather
# than in a module of their own because every feature module needs them and
# none of them may import `app.py`: `rounds.py` asking whether a card is this
# visitor's would otherwise pull the whole route table in behind it.
#
# They read `session`, `g`, `ADMIN_EMAILS` and the two data modules, and they
# define nothing that `app.py` defines -- which is what keeps the import
# one-directional, the same property the boot block above has.
#
# **Callers reach them through this module** -- `web.is_admin()`, not a copy
# bound at import. That is #436's rule and it is what makes this move cost no
# test churn: one `monkeypatch.setattr("web.current_settings", ...)` reaches
# `app.py`, `cards_owner_filter()` here, and whatever module is written next.
# The names are still bound into `app.py` as well, for the tests that call
# `app_module.is_admin()` rather than patch it.

def _current_email():
    """Email of the signed-in visitor, or None for anonymous visitors."""
    return (session.get("user") or {}).get("email")


# Refusals shown when a delete is not this visitor's to make (#162). The first
# is the wording given in the issue; the second is #125's problem — no identity
# at all — rather than the card belonging to someone else.
DELETE_NOT_YOURS = ("This card was created by admin or another user. "
                    "You cannot delete the card.")
DELETE_SIGN_IN_PROMPT = ("Sign in with Google to delete cards you have added.")
# #176: the same two refusals, for editing.
EDIT_NOT_YOURS = ("This card was created by admin or another user. "
                  "You cannot edit the card.")
EDIT_SIGN_IN_PROMPT = "Sign in with Google to edit cards you have added."
# #177: moving a card between topics is an edit, with its own wording.
MOVE_NOT_YOURS = ("This card was created by admin or another user. "
                  "You cannot move the card.")
MOVE_SIGN_IN_PROMPT = "Sign in with Google to move cards you have added."
# #125: shown when a visitor with no account tries to write to the database.
# The wording is the issue's own, so the popup says what was specified.
ADD_SIGN_IN_PROMPT = ("Please sign in with Google to make any changes "
                      "of the database.")
# #165: an anonymous visitor has no account, and neither does a sign-in whose
# users row could not be written (#148).
SIGN_IN_TO_DELETE_ACCOUNT = "Sign in with Google to delete your account."
# The admin keeps the site running; removing that account from inside the app
# is a footgun with no upside. Admin-ness lives in ADMIN_EMAILS (#158), so the
# way out is to stop being an admin first — then the account deletes normally.
ADMIN_ACCOUNT_UNDELETABLE = (
    "Admin account cannot be deleted. Remove this address from ADMIN_EMAILS "
    "first, then delete the account.")


def current_block():
    """This visitor's block, or None (issue #126). Cached for the request.

    One query per signed-in request, not per call: the widget, the card pages
    and the save routes all ask, and `g` is exactly the scope the answer is
    valid for. Anonymous visitors have no account to block, so they never
    reach the database here.

    A dead database means no block is visible. That is the same tolerance the
    rest of the app already has (a failed users-row write still signs you in),
    and it fails in the direction that keeps the site usable — a blocked
    account gets its restrictions back the moment the database answers again,
    and #125 still refuses every write while `_current_user_id()` is unusable.
    """
    if "kf_block" not in g:
        try:
            g.kf_block = utils.get_user_block(_current_user_id())
        except Exception:
            app.logger.exception("Could not read the block state")
            g.kf_block = None
    return g.kf_block


def is_blocked():
    """Whether this request's visitor is a blocked account (issue #126)."""
    return current_block() is not None


def blocked_notice():
    """What a blocked user is told when they try to change something (#126).

    Names an admin address so the message is an instruction rather than a
    dead end — that is the whole of the issue's "shown the admin's address so
    they can ask for access back". With no ADMIN_EMAILS configured there is
    nobody to name, so the sentence stops after the fact.
    """
    admin = next(iter(sorted(ADMIN_EMAILS)), None)
    if admin:
        return ("Your account is blocked, so you cannot change the database. "
                f"Write to {admin} to ask for access.")
    return "Your account is blocked, so you cannot change the database."


def can_add_cards():
    """Whether this request's visitor may write cards (issue #125).

    Signed in *and* carrying a users-row id. The id is the requirement rather
    than a name in the session, because #89 records who added a card and a
    card with no owner cannot be deleted by its author later (#162) — so a
    sign-in whose users row could not be written is refused here too. That is
    the fail-closed direction: the alternative writes an unowned card that
    nobody but an admin can ever remove.

    Admin-ness is not consulted: an admin is signed in, so they already pass.

    A blocked account (#126) is refused here too — same answer, different
    reason, which is what `add_refusal()` is for.
    """
    return _current_user_id() is not None and not is_blocked()


def add_refusal():
    """Why this visitor may not add cards, or None if they may.

    Two refusals share one path: no account at all (#125) and an account that
    has been blocked (#126). The distinction only ever shows in the wording,
    so it lives here rather than at each of the four call sites.
    """
    if can_add_cards():
        return None
    return blocked_notice() if is_blocked() else ADD_SIGN_IN_PROMPT


def can_delete_card(card):
    """Whether this request's visitor may delete `card` (issue #162).

    Presentation only — it decides whether the cross is greyed. The rule is
    enforced again in delete_card(), which is what actually protects the row;
    this exists so the UI does not offer an action that will be refused.
    """
    if is_blocked():
        # Checked before admin-ness: an admin who blocked their own account
        # is telling the app something, and #165 already refuses to let the
        # admin delete that account, so this cannot lock the site's owner out
        # of anything permanent.
        return False
    if is_admin():
        return True
    user_id = _current_user_id()
    if user_id is None:
        return False
    return card.get("added_by_user_id") == user_id


def _card_refusal(card, not_yours, sign_in_prompt):
    """Why this visitor may not change `card`, or None if they may.

    The three ways to change a card — delete (#162), edit (#176) and move
    (#177) — share one rule and differ only in wording, so they share this and
    supply their own sentences. A blocked account (#126) is told that instead,
    since it is the more informative answer.
    """
    if can_delete_card(card):
        return None
    if is_blocked():
        return blocked_notice()
    return not_yours if _current_user_id() is not None else sign_in_prompt


def can_move_card(card):
    """Whether this visitor may move `card` to another topic (issue #177).

    A move is an edit: the card sits in a shared topic, but it is still its
    author's, so moving someone else's card is closer to editing theirs than
    to organising your own.
    """
    return can_delete_card(card)


def move_refusal(card):
    """The tooltip explaining why the move control is greyed, or None."""
    return _card_refusal(card, MOVE_NOT_YOURS, MOVE_SIGN_IN_PROMPT)


def can_edit_card(card):
    """Whether this visitor may edit `card` (issue #176).

    Deliberately the same rule as deleting (#162): the admin may change any
    card, a signed-in user only their own, and nobody else at all. Editing a
    card's word is as destructive as removing it — the person who added it
    would find it silently different — so a weaker rule here would undo #162.

    Kept as its own name rather than a call site of can_delete_card so that if
    the two ever do diverge, the change is a visible one.
    """
    return can_delete_card(card)


def edit_refusal(card):
    """The tooltip explaining why the pencil is greyed, or None if it isn't."""
    return _card_refusal(card, EDIT_NOT_YOURS, EDIT_SIGN_IN_PROMPT)


def delete_refusal(card):
    """The tooltip explaining why the cross is greyed, or None if it isn't."""
    return _card_refusal(card, DELETE_NOT_YOURS, DELETE_SIGN_IN_PROMPT)


def is_admin():
    """Whether this request's visitor is an administrator (issue #158).

    Three things must hold, and the check fails closed if any is missing: the
    visitor is signed in, Google reported their email as verified, and the
    address is in ADMIN_EMAILS. Requiring `email_verified` is what stops an
    account that merely *claims* a listed address from inheriting the
    privileges; a session predating this check carries no such claim and is
    therefore not admin until its owner signs in again.

    Nothing uses the privilege yet — #126 (blocking) and #162 (deleting any
    card) are what will ask.
    """
    user = session.get("user") or {}
    if not user.get("email_verified"):
        return False
    email = (user.get("email") or "").strip().lower()
    return bool(email) and email in ADMIN_EMAILS


def _current_user_id():
    """Row id of the signed-in visitor (#89), or None.

    None covers three cases that the database cannot tell apart afterwards and
    does not need to: an anonymous visitor, a sign-in whose users row could not
    be written (#148), and any card saved before the column existed.

    This is the only place the id may come from. It must never be read from
    request data — the review popup posts hidden fields, so a browser could
    otherwise attribute its cards to somebody else.
    """
    return (session.get("user") or {}).get("id")


def _identity_token():
    """Opaque stamp for this identity, or None for an anonymous visitor (#170).

    The chat widget keeps its transcript in localStorage and has to know
    whether a stored thread belongs to whoever is signed in *now* — clearing
    on the way out cannot cover an identity change the browser never sees,
    like a session expiring or being repaired server-side.

    A salted digest rather than the id or the email, because this is written
    into localStorage: readable by anything on the origin and still there
    after sign-out. Equality is the only thing the widget asks of it.

    The token changes if SECRET_KEY does, which discards stored threads once.
    """
    key = _current_user_id() or _current_email()
    if not key:
        return None
    salted = f"{app.secret_key}:{key}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()[:16]


def cards_owner_filter():
    """The owner to restrict card reads to, or None for the shared deck (#127).

    None whenever the filter cannot mean anything: the setting is off, or the
    visitor has no account to own cards. An anonymous visitor is the case that
    matters — they share config-default.json, so if the toggle were ever left
    on there they would all see an empty site with no way to change it back
    (#102 makes that config read-only for them).

    Returning None rather than a falsy id also keeps the SQL honest: the query
    layer treats None as "no filter", never as "owned by nobody".
    """
    if not current_settings()["individual_cards"]:
        return None
    return _current_user_id()


def viewer():
    """Who is asking, for #382's topic visibility: `(user id, is admin)`.

    Deliberately **not** `cards_owner_filter()`, and passed beside it rather
    than instead of it. That one is #127's setting -- what this visitor asked
    to look at, None when they asked for everything. This is who they *are*,
    which no setting changes, and it is what decides whether a private topic
    exists for them at all.

    Every card read takes both, because the two answer different halves of the
    same query: "whose cards do you want" and "whose topics may you see".

    A dict, spread with `**viewer()`, so the two arrive as **keywords**. Two
    bare positionals after `cards_owner_filter()` read as three anonymous
    filters at twenty-five call sites, and every stub in the test suite would
    have to know their order to stand in for one of these functions.
    """
    return {"viewer_id": _current_user_id(), "admin": is_admin()}


def current_settings():
    """Settings for this request (issue #86): the signed-in user's own config
    file, or the shared default config for anonymous visitors. Always returns a
    complete, valid dict — a missing or corrupt file falls back to defaults."""
    return settings_store.load(_current_user_id(), _current_email())


def _sections_for_visitor(owner=None):
    """`utils.get_topics_by_section()` in the order this visitor asked for (#363).

    The single reader of `alphabetical_topics`, and the reason is the shape of
    the bug it prevents: four pages list topics — the browse tiles, the
    picker, `/topics.json` behind the widget's own re-render, and Mykola's
    context — and a fifth that asked the database directly would quietly be
    the one page ordered differently from the rest. Calling this is how a page
    gets the ordering; there is nothing to remember.

    Raises what the query raises. Callers that must survive a dead database
    already catch it, and `_visible_sections()` is the one that does it for
    the picker.
    """
    if owner is None:
        owner = cards_owner_filter()
    return utils.get_topics_by_section(
        owner, alphabetical=current_settings()["alphabetical_topics"],
        **viewer())


def _private_marks():
    """The padlocks this visitor's browse page draws (#382), or `{}`.

    A map beside the sections rather than a third element in their pairs --
    #223's icons set that precedent, and for the same reason: the pair is read
    by the index page, the move dialog and the Mykola widget's own renderer.

    A dead database costs the padlocks and nothing else, exactly as it costs
    the sections themselves one line above.
    """
    try:
        return utils.private_topics(**viewer())
    except Exception:
        app.logger.exception("Could not list the private topics")
        return {}
