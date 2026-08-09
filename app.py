import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from authlib.integrations.flask_client import OAuth
from jinja2 import ChoiceLoader, Environment, FileSystemLoader
from werkzeug.middleware.proxy_fix import ProxyFix

import applog
import games
import settings_store
from parsers import lookup_word, parse_notes_preview
from utils import (
    claim_anonymous_message,
    delete_flashcard,
    delete_user,
    resolve_user_cards,
    flashcard_word_exists,
    get_db_connection,
    get_flashcards_by_topic,
    get_flashcards_by_topics,
    get_topics,
    get_topics_by_section,
    get_user_block,
    find_duplicate,
    move_flashcard,
    set_preferred_name,
    update_flashcard,
    save_flashcard,
    upsert_user,
)

app = Flask(__name__)
# Needed for flash messages (session cookie). Override in production:
# set the SECRET_KEY environment variable on PythonAnywhere.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# How long a signed-in session survives once marked permanent (see the OAuth
# callback). Keeps the visitor greeted by name across browser restarts without
# any server-side storage — the identity still lives only in the signed cookie.
app.permanent_session_lifetime = timedelta(days=30)

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

# --- Mykola AI chat, imported from the ai_agent repo (NOT duplicated here) ---
# The agent lives in a separate repo; point AI_AGENT_PATH at its checkout.
# Default: a sibling folder next to this repo (matches the PythonAnywhere
# layout /home/<user>/ai_agent alongside /home/<user>/kuantorflow).
AI_AGENT_PATH = os.environ.get(
    "AI_AGENT_PATH", str(Path(__file__).resolve().parent.parent / "ai_agent")
)
if AI_AGENT_PATH not in sys.path:
    sys.path.insert(0, AI_AGENT_PATH)

try:
    # Importing pulls in the agent's own .env (ANTHROPIC_API_KEY) and knowledge
    # base module. If the repo or its deps are missing, Mykola is simply disabled.
    from agent import MykolaAgent, api_error_response
    MYKOLA_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the deployment environment
    MYKOLA_AVAILABLE = False

_mykola_agent = None


class _RequestPathProxy:
    """Expose request with an overridden path for imported template rendering."""
    def __init__(self, original_request, path_override: str):
        self._original_request = original_request
        self.path = path_override

    def __getattr__(self, name):
        return getattr(self._original_request, name)


def _mykola_template_url_for(endpoint, **values):
    """Map ai_agent template endpoint names to kuantorflow routes."""
    if endpoint == "home":
        return url_for("mykola_chat_page", **values)
    if endpoint == "about":
        return url_for("mykola_about", **values)
    if endpoint == "static":
        return url_for("mykola_static_file", **values)
    return url_for(endpoint, **values)


def _render_ai_agent_template(template_name: str, **context):
    """Render a template straight from ai_agent/templates (no duplication)."""
    env = Environment(loader=FileSystemLoader(os.path.join(AI_AGENT_PATH, "templates")))
    request_proxy = _RequestPathProxy(request, "/") if template_name == "index.html" else request
    env.globals.update(url_for=_mykola_template_url_for, request=request_proxy)
    template = env.get_template(template_name)
    return template.render(**context)


def _current_first_name():
    """What Mykola should call the signed-in visitor (issue #148).

    Resolution order: their chosen `preferred_name`, then Google's
    `given_name` claim, then the first word of the display name. The last step
    is a guess — it can pick a surname in a family-name-first locale — which is
    why it only runs when Google supplied no given name.

    `given_name` is used **whole, on purpose**: someone whose given name is
    "Anna Maria" is addressed as "Anna Maria". Shortening it to the first word
    was considered and rejected — it is the app guessing at a nickname, and
    the user already has a better way to say what they want to be called.
    That is what `preferred_name` is for (a Settings field, or telling Mykola
    in conversation, ai_agent#62). Please don't "fix" this to `.split()[0]`.
    """
    user = session.get("user") or {}
    preferred = (user.get("preferred_name") or "").strip()
    if preferred:
        return preferred
    given = (user.get("given_name") or "").strip()
    if given:
        return given
    name = (user.get("name") or "").strip()
    return name.split()[0] if name else None


def _record_sign_in(info):
    """Persist the signed-in identity (#148); return (user_id, preferred_name).

    Returns (None, None) if the row can't be written — an unreachable database
    must not cost the user their login, the same way get_topics() and
    _word_already_saved() already tolerate one.
    """
    google_sub = (info.get("sub") or "").strip()
    email = (info.get("email") or "").strip()
    if not google_sub or not email:
        # sub is mandatory in OIDC, so this means something is badly wrong —
        # sign in anyway, without a row.
        app.logger.warning("Google sign-in without sub/email; not recording it")
        return None, None
    try:
        return upsert_user(
            google_sub, email,
            display_name=_claim(info, "name"),
            given_name=_claim(info, "given_name"),
            family_name=_claim(info, "family_name"),
        )
    except Exception:
        app.logger.exception("Could not record the Google sign-in")
        return None, None


def _claim(info, key):
    """A Google claim as a stored value: blank and missing both become NULL."""
    return (info.get(key) or "").strip() or None


def _email_verified(info):
    """Google's `email_verified` claim as a strict bool (issue #158).

    The claim is a real bool in the ID token but a string in some userinfo
    responses, and `bool("false")` is True — so compare explicitly rather than
    trusting truthiness. Anything unrecognised counts as not verified.
    """
    claim = info.get("email_verified")
    if isinstance(claim, bool):
        return claim
    return str(claim).strip().lower() == "true"


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
            g.kf_block = get_user_block(_current_user_id())
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


def current_settings():
    """Settings for this request (issue #86): the signed-in user's own config
    file, or the shared default config for anonymous visitors. Always returns a
    complete, valid dict — a missing or corrupt file falls back to defaults."""
    return settings_store.load(_current_user_id(), _current_email())


def _save_and_log(entry, source):
    """Save one card and record the outcome in logs/cards.log (#30).

    Every card written by the app goes through here or through the explicit
    applog calls next to the other save_flashcard() call sites — keep it that
    way when adding a new save path.

    Returns True when a row was actually written (False = duplicate).

    Being the single funnel is also what makes #89 one change instead of four:
    every save path records its owner here.

    It is also where #125 is enforced, for the same reason: a save path that
    forgets to ask `can_add_cards()` first fails loudly here instead of
    quietly writing. Callers that face a person check beforehand, so the
    visitor gets the sign-in prompt rather than an error.
    """
    refusal = add_refusal()
    if refusal:
        applog.card_add_denied(entry, source=source, user=_current_email(),
                               reason="blocked" if is_blocked() else "anonymous")
        raise PermissionError(refusal)
    card_id = save_flashcard(entry, added_by_user_id=_current_user_id())
    if card_id is None:
        applog.card_skipped(entry, source=source, user=_current_email())
        return False
    applog.card_created(entry, source=source, user=_current_email(),
                        card_id=card_id)
    return True


def duplicate_notice(entries):
    """Extra sentence for a save skipped as a duplicate (#186), or None.

    Duplicate prevention (#101) is global, but #127 hides other people's cards
    — so "already in the database" can be said about a card the visitor cannot
    see, which reads as the app contradicting itself. Naming the setting turns
    a puzzle into a choice they can act on.

    Only ever an *addition* to the existing message: the plain wording is
    correct whenever the blocking card is one they can actually find.
    """
    if not current_settings()["individual_cards"]:
        return None
    owner = _current_user_id()
    try:
        for entry in entries:
            existing = find_duplicate(entry.get("word"), entry.get("pos"))
            if existing and existing[1] != owner:
                return ("It is in the shared deck, hidden from you by your "
                        "'Use only individual cards' setting.")
    except Exception:
        # A dead database here costs a nicety, not the save path's answer.
        app.logger.exception("Could not check whether the duplicate is hidden")
    return None


def _word_already_saved(word):
    """Whether the word already has cards (issue #145). A DB error is treated
    as 'unknown' → no warning, so lookups keep working when the DB is down."""
    try:
        return flashcard_word_exists(word)
    except Exception:
        return False


def _hidden_languages():
    """Language names this identity has hidden in Settings (#46/#79/#111),
    in the form the agent's whitelist expects — e.g. ["Russian"]."""
    prefs = current_settings()
    hidden = []
    if not prefs["show_ukrainian"]:
        hidden.append("Ukrainian")
    if not prefs["show_russian"]:
        hidden.append("Russian")
    return hidden


def _agent_kwargs(method, away_hours=None):
    """kwargs for an agent call, holding only what the installed ai_agent
    version supports. Feature-detected so the chat keeps working even if the
    ai_agent side hasn't been updated yet."""
    params = inspect.signature(method).parameters
    kwargs = {}
    first_name = _current_first_name()
    if first_name and "user_name" in params:
        kwargs["user_name"] = first_name
    hidden = _hidden_languages()
    if hidden and "hidden_languages" in params:
        kwargs["hidden_languages"] = hidden
    # ai_agent#54: how long the learner was silent, so a restart recap can
    # open by acknowledging the break. Older agents simply don't take it.
    if away_hours is not None and "away_hours" in params:
        kwargs["away_hours"] = away_hours
    return kwargs


def _agent_answer(question, history):
    """Call the agent with the signed-in first name and the hidden-language
    preferences, where the installed ai_agent version supports them."""
    agent = get_mykola()
    return agent.answer(question, history, **_agent_kwargs(agent.answer))


SIGN_IN_PROMPT = ("You've used your free messages with Mykola. "
                  "Sign in with Google to keep chatting.")
BUSY_PROMPT = ("Mykola has answered a lot of questions today. "
               "Sign in with Google to keep chatting, or come back tomorrow.")


def _anonymous_quota_refusal():
    """Refuse an anonymous chat message that is over quota (#164), or None to
    let it through. Signed-in visitors are never limited.

    Both counters are best-effort in the same direction: if the database is
    unreachable the daily ceiling can't be enforced, and the message is
    allowed rather than a dead database silencing Mykola for everyone.
    """
    if session.get("user"):
        return None

    used = session.get("anon_messages", 0)
    if ANONYMOUS_MESSAGE_LIMIT and used >= ANONYMOUS_MESSAGE_LIMIT:
        applog.anonymous_limit_hit("session", used, ANONYMOUS_MESSAGE_LIMIT)
        return jsonify({"error": SIGN_IN_PROMPT, "sign_in_required": True}), 402

    try:
        allowed, today = claim_anonymous_message(ANONYMOUS_DAILY_LIMIT)
    except Exception:
        app.logger.exception("Could not count the anonymous message")
        allowed, today = True, 0
    if not allowed:
        applog.anonymous_limit_hit("daily", today, ANONYMOUS_DAILY_LIMIT)
        return jsonify({"error": BUSY_PROMPT, "sign_in_required": True}), 402

    session["anon_messages"] = used + 1
    return None


def _handle_mykola_chat_request():
    """Shared JSON chat handler for widget and full ai_agent-style page."""
    if not MYKOLA_AVAILABLE:
        return jsonify({"error": "Mykola is not available on this server."}), 503

    # A blocked account (#126) does not get the widget, but hiding it is
    # presentation: this is the refusal that holds for a request made by hand.
    # Before the length and content checks, so a blocked visitor cannot use
    # the endpoint's answers to probe anything.
    if is_blocked():
        applog.mykola_denied(user=_current_email())
        return jsonify({"error": blocked_notice()}), 403

    if request.content_length and request.content_length > MAX_MYKOLA_REQUEST_BYTES:
        return jsonify({"error": "Your message is too long. Please shorten it and try again."}), 413

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    history = data.get("history", [])
    chat_id = _safe_chat_id(data.get("chat_id"))
    if not question:
        return jsonify({"error": "Please type a question."}), 400

    # Anonymous quota (#164) — checked before the model is called, so a
    # refused message costs nothing.
    refusal = _anonymous_quota_refusal()
    if refusal:
        return refusal

    try:
        result = _agent_answer(question, history)
        _append_chat_log(chat_id, question, result.get("response", ""))
        result["chat_id"] = chat_id
        return jsonify(result)
    except Exception as e:  # format Anthropic errors nicely, log the rest
        import anthropic
        if isinstance(e, anthropic.APIError):
            body, status = api_error_response(e)
            return jsonify(body), status
        app.logger.exception("Mykola chat failed")
        return jsonify({"error": "Internal server error. Please try again later."}), 500


def _new_chat_id() -> str:
    """Readable chat id: the date & time the chat started, plus a short
    suffix so two chats starting the same second get separate logs
    (ai_agent#25). The widget reuses the id for the whole conversation."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid.uuid4().hex[:4]


def _safe_chat_id(raw_chat_id: str | None) -> str:
    """Allow only safe filename chars, minting a readable id if absent."""
    if not raw_chat_id:
        return _new_chat_id()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw_chat_id.strip())
    return safe[:64] or _new_chat_id()


def _safe_email_prefix(email: str | None) -> str | None:
    """Filesystem-safe directory name from the part of an email before the @."""
    prefix = (email or "").split("@", 1)[0].strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]", "_", prefix).strip("._")
    return safe[:64] or None


def _current_user_log_dir() -> Path:
    """Log directory for this request: mykola_logs/<user id>/ for signed-in
    visitors (ai_agent#30, re-keyed in #174), the shared mykola_logs/ otherwise.

    Keyed on the id rather than the email prefix because the prefix was neither
    stable nor unique — an address change orphaned a user's whole chat history,
    and two addresses sharing a local part fed one person's conversations into
    another's welcome-back recap.
    """
    user_id = _current_user_id()
    if user_id is None:
        return LOG_DIR
    user_dir = LOG_DIR / str(user_id)
    if not user_dir.is_dir():
        _migrate_log_dir(user_dir)
    user_dir.mkdir(exist_ok=True)
    _write_log_dir_marker(user_dir, user_id)
    return user_dir


def _migrate_log_dir(user_dir: Path) -> None:
    """Move this visitor's pre-#174 email-keyed chat folder onto its id-keyed
    name. On read rather than by a script, for the same reason as the settings
    store: a user who hasn't signed in since #148 has logs but no users row."""
    prefix = _safe_email_prefix(_current_email())
    if not prefix:
        return
    legacy = LOG_DIR / prefix
    if not legacy.is_dir() or legacy == user_dir:
        return
    try:
        os.replace(legacy, user_dir)
    except OSError:
        app.logger.exception("Could not migrate the chat log folder for #174")


def _write_log_dir_marker(user_dir: Path, user_id) -> None:
    """Record whose folder this is, so a directory listing stays readable (#174).

    The full email, not the prefix: prefixes are exactly what collide, so a
    marker reading 'anton' would not tell two anton@… accounts apart.

    Rewritten only when the contents would change, so a chat doesn't rewrite it
    every message. _user_log_files() globs chat_*.txt, so this file can never be
    read as a conversation or reach Mykola's recap.
    """
    user = session.get("user") or {}
    lines = (f"id: {user_id}\n"
             f"email: {user.get('email') or 'unknown'}\n"
             f"name: {user.get('name') or 'unknown'}\n")
    marker = user_dir / "user.txt"
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8") == lines:
            return
        marker.write_text(lines, encoding="utf-8")
    except OSError:
        pass  # a missing marker is cosmetic; never break the chat over it


def _user_log_files() -> list[Path]:
    """Signed-in user's chat logs, newest first ([] for anonymous visitors)."""
    user_dir = _current_user_log_dir()
    if user_dir == LOG_DIR:
        return []
    return sorted(user_dir.glob("chat_*.txt"), key=lambda p: p.stat().st_mtime,
                  reverse=True)


def _read_user_logs(max_chars: int = 12000, max_files: int = 3) -> str:
    """Most recent chat-log text of the signed-in user, chronological order.

    Capped at max_files logs (ai_agent#39: keep the model's context focused —
    a future settings scheme may widen this) with max_chars as the secondary
    guard. Empty string for anonymous users or no history."""
    collected, total = [], 0
    for path in _user_log_files()[:max_files]:  # newest first
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        collected.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(reversed(collected))[-max_chars:]


# Farewell phrases that, in today's last message, mean the learner already
# said goodbye — Mykola then wishes them a good rest instead of a recap.
FAREWELL_RE = re.compile(
    r"\b(good\s*bye|bye|good\s*night|see\s+you|farewell)\b", re.IGNORECASE
)


def _last_user_message(log_text: str) -> str:
    """The learner's final message in a chat-log file ('' if none found)."""
    sections = re.findall(r"User:\n(.*?)\n\nMykola:", log_text, re.DOTALL)
    return sections[-1].strip() if sections else ""


def _said_farewell_today() -> bool:
    """True when the signed-in user's newest log was written today and their
    last message in it was a farewell (ai_agent#39)."""
    files = _user_log_files()
    if not files:
        return False
    newest = files[0]
    try:
        if datetime.fromtimestamp(newest.stat().st_mtime).date() != datetime.now().date():
            return False
        return bool(FAREWELL_RE.search(_last_user_message(
            newest.read_text(encoding="utf-8"))))
    except OSError:
        return False


# One logged exchange starts with its "[YYYY-MM-DD HH:MM:SS]" stamp.
EXCHANGE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]$", re.M)


def _split_exchanges(text: str) -> list[str]:
    """One chat-log file's text split into its individual exchanges."""
    stamps = list(EXCHANGE_RE.finditer(text))
    return [text[m.start():(stamps[i + 1].start() if i + 1 < len(stamps) else len(text))].strip()
            for i, m in enumerate(stamps)]


def _last_exchanges(count: int = 3) -> str:
    """The signed-in user's last `count` exchanges with Mykola, oldest first.

    The welcome-back recap (ai_agent#30) reviews whole log *files*; a restart
    after a break reviews the last few *messages* (ai_agent#54), so it stays
    focused on where the conversation actually stopped. '' when there is no
    history (including every anonymous visitor, who has no per-user logs).
    """
    collected: list[str] = []
    for path in _user_log_files():             # newest file first
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # take this file's newest exchanges first, then walk further back
        collected = _split_exchanges(text)[-(count - len(collected)):] + collected
        if len(collected) >= count:
            break
    return "\n\n".join(collected[-count:])


def _last_chat_activity() -> datetime | None:
    """When the signed-in user last exchanged a message with Mykola, from the
    newest log file's timestamp. None for anonymous visitors and newcomers."""
    files = _user_log_files()
    if not files:
        return None
    try:
        return datetime.fromtimestamp(files[0].stat().st_mtime)
    except OSError:
        return None


def _client_last_activity(raw) -> datetime | None:
    """The widget's own 'last message' stamp (epoch milliseconds).

    Anonymous visitors have no per-user logs, so their break can only be
    measured from the browser that holds the conversation. Anything
    unparseable — or in the future — is ignored (ai_agent#54).
    """
    try:
        moment = datetime.fromtimestamp(float(raw) / 1000)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return moment if moment <= datetime.now() else None


def _chat_log_path(chat_id: str) -> Path | None:
    """Where this conversation is written, or None when nobody is signed in.

    The widget promises an anonymous visitor that "nothing is kept under your
    name", and until #163 that held only in a lawyer's reading: their
    conversation went to the shared mykola_logs/ root instead of a per-user
    folder, but both sides of it were still on the server.

    Nothing ever read those files — _user_log_files() returns [] for the shared
    root, so the welcome-back recap (ai_agent#30) and the restart
    (ai_agent#54) skip them — so declining to write them costs no behaviour at
    all. It only makes the sentence true as a visitor would read it.
    """
    user_dir = _current_user_log_dir()
    if user_dir == LOG_DIR:          # the same test _user_log_files() makes
        return None
    return user_dir / f"chat_{chat_id}.txt"


def _start_chat_log(chat_id: str, away_hours: float, recap: str | None) -> None:
    """Open the restarted conversation's log file (ai_agent#54) with a note of
    why it exists, so the break is visible in the history itself."""
    log_path = _chat_log_path(chat_id)
    if log_path is None:             # anonymous: not written at all (#163)
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}]\n")
            f.write("--- Chat restarted automatically after "
                    f"{away_hours:.1f}h away ---\n")
            if recap:
                f.write("\nMykola:\n" + recap.strip() + "\n")
    except OSError:
        app.logger.exception("Could not start the restarted chat log")


def _append_chat_log(chat_id: str, user_text: str, assistant_text: str) -> None:
    """Append one user/assistant exchange to chat_<chat_id>.txt in the
    signed-in user's subdirectory. An anonymous conversation is not written
    down (#163)."""
    log_path = _chat_log_path(chat_id)
    if log_path is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}]\n")
        f.write("User:\n")
        f.write((user_text or "").strip() + "\n\n")
        f.write("Mykola:\n")
        f.write((assistant_text or "").strip() + "\n")
        f.write("\n" + ("=" * 60) + "\n\n")

# When the agent repo is present, let kuantorflow's Jinja also find its
# templates (e.g. the shared _mykola_about.html partial). kuantorflow's own
# templates stay first, so they win on any name clash (base.html, etc.).
if MYKOLA_AVAILABLE:
    app.jinja_loader = ChoiceLoader([
        app.jinja_loader,
        FileSystemLoader(os.path.join(AI_AGENT_PATH, "templates")),
    ])


@app.context_processor
def inject_mykola_media():
    """Resolve Mykola asset names to the /mykola-media route (which serves them
    straight from the ai_agent repo). Mirrors the helper ai_agent defines for
    itself, so the shared About partial's asset URLs work here too."""
    return {"mykola_media": lambda f: url_for("mykola_media_file", filename=f)}


@app.route("/mykola-media/<path:filename>")
def mykola_media_file(filename):
    """Serve Mykola's images/video from the ai_agent repo — no copies in this
    repo. Behind the keyword gate like everything else."""
    if not MYKOLA_AVAILABLE:
        abort(404)
    return send_from_directory(os.path.join(AI_AGENT_PATH, "static", "img"), filename)


@app.route("/mykola-static/<path:filename>")
def mykola_static_file(filename):
    """Serve static files directly from ai_agent/static for full chat page."""
    if not MYKOLA_AVAILABLE:
        abort(404)
    return send_from_directory(os.path.join(AI_AGENT_PATH, "static"), filename)


@app.route("/mykola/about")
def mykola_about():
    """About-Mykola page: kuantorflow chrome wrapping the shared ai_agent partial."""
    if not MYKOLA_AVAILABLE:
        abort(404)
    return render_template("mykola_about.html")


@app.route("/mykola/chat-page")
def mykola_chat_page():
    """Open ai_agent's own chat page template from this app."""
    if not MYKOLA_AVAILABLE:
        abort(404)
    return _render_ai_agent_template("index.html")


def _save_card_from_chat(entry):
    """Card saver injected into the agent: persists a flashcard Mykola was
    asked to add in chat, through the same save_flashcard mechanism as the
    Look up & save flow (issue: ai_agent#20). A duplicate word+pos is
    skipped by save_flashcard (issue #101); Mykola still reports the card,
    which is accurate either way — it is in the database.

    An anonymous visitor's card is refused (#125) by _save_and_log raising:
    the agent turns an exception from its card_saver into an error the model
    relays, so Mykola says he cannot save it and why, instead of claiming a
    card that was never written."""
    _save_and_log(entry, source="Mykola chat")
    return entry


def _save_preferred_name_from_chat(name):
    """Name saver injected into the agent: stores what the learner asked to be
    called (ai_agent#62), or clears it when `name` is None.

    Refuses an anonymous learner by raising, exactly as the card saver does
    (#125): the agent turns the exception into an error status and Mykola says
    he cannot remember it without an account, rather than claiming he will.

    The session copy is updated too, so the very next message — and the next
    recap — already use the new name. Without that the change would only
    appear after signing in again, since `_current_first_name()` reads the
    session, not the database.
    """
    user_id = _current_user_id()
    if user_id is None:
        raise PermissionError(
            "Sign in with Google and I shall remember what to call you.")
    if not set_preferred_name(user_id, name):
        raise RuntimeError("I could not find your account to note that in.")

    user = dict(session.get("user") or {})
    user["preferred_name"] = name
    session["user"] = user
    applog.preferred_name_set(user_id, name, user=_current_email())
    return name


def get_mykola():
    """Lazily build the MykolaAgent (loads the knowledge base) on first use."""
    global _mykola_agent
    if _mykola_agent is None:
        # ai_agent emits to its own loggers and, being a library, does not
        # decide where they write. Point them at logs/mykola.log before the
        # first call, or every line it logs is silently discarded
        # (ai_agent#71, #75).
        applog.attach_agent_logs()
        # Inject our DB writer when the installed agent supports it —
        # feature-detected so older ai_agent checkouts keep working.
        # Feature-detected, so the two repos can be deployed in either order:
        # an older ai_agent checkout simply doesn't get the newer saver.
        kwargs = {}
        accepted = inspect.signature(MykolaAgent.__init__).parameters
        if "card_saver" in accepted:
            kwargs["card_saver"] = _save_card_from_chat
        if "name_saver" in accepted:                      # ai_agent#62
            kwargs["name_saver"] = _save_preferred_name_from_chat
        if "topic_reader" in accepted:                    # ai_agent#68
            kwargs["topic_reader"] = _topics_for_chat
        if "card_reader" in accepted:                     # ai_agent#68
            kwargs["card_reader"] = _cards_for_chat
        _mykola_agent = MykolaAgent(**kwargs)
    return _mykola_agent


# --- what Mykola may read (ai_agent#68) ----------------------------------
# The agent is a process-wide singleton, but these run *inside* a request —
# the model calls a tool, we answer it, all within /mykola/chat. So visibility
# is resolved here, at call time, not captured when the agent was built. Doing
# it the other way round would freeze the first visitor's view of the deck and
# serve it to everybody afterwards.

def _topics_for_chat():
    """Topics and card counts, as this visitor is allowed to see them."""
    owner = cards_owner_filter()
    return [{"topic": name, "cards": count}
            for _section, topics in get_topics_by_section(owner)
            for name, count in topics]


# The translation column each hideable language lives in (#46/#79).
_TRANSLATION_COLUMNS = {"Ukrainian": "translation_ukr",
                        "Russian": "translation_rus"}


def _cards_for_chat(topic, limit):
    """One topic's cards, filtered exactly as the browse page filters them.

    Through `get_flashcards_by_topic()` with `cards_owner_filter()`, so #127
    holds here too: a learner who has hidden other people's cards must not
    have Mykola read them out. Anything else would make the chat a way around
    a setting the rest of the site honours.

    A hidden language is **removed from the row**, not merely left unmentioned
    (#46/#79). The agent is already told in its system prompt not to show one,
    but an instruction is not an enforcement: everywhere else on the site the
    hidden language is absent from what the page can render, and the chat
    should be no weaker. Deleting it here means the only copy the model ever
    sees is one the learner has agreed to see.
    """
    hidden = [_TRANSLATION_COLUMNS[name] for name in _hidden_languages()
              if name in _TRANSLATION_COLUMNS]
    cards = get_flashcards_by_topic(topic, cards_owner_filter())[:limit]
    if not hidden:
        return cards
    return [{k: v for k, v in card.items() if k not in hidden}
            for card in cards]


@app.context_processor
def inject_mykola():
    """Expose whether the chat widget should render.

    A blocked account (#126) does not get the widget. That is presentation —
    the endpoints refuse the request themselves — but leaving a chat box that
    answers every message with a refusal would be worse than not offering it.
    """
    return {
        "mykola_enabled": MYKOLA_AVAILABLE and not is_blocked(),
        "app_boot_id": APP_BOOT_ID,
        "mykola_identity": _identity_token(),
    }


@app.before_request
def drop_identity_from_before_the_users_table():
    """Sign out a session created before #148, so the next sign-in repairs it.

    Those sessions carry a name, an email and a picture but no `id` key —
    the users row they would point at was never written, and nothing else
    ever fills it in. The visitor still looks signed in, while every card
    they save is attributed to nobody (#89). Session cookies here are
    permanent, so that can go on for 30 days without a visible symptom.

    Dropping the identity costs one sign-in and fixes it for good: the OAuth
    callback writes the users row and puts its id in the session.

    A stored `id` of None is a *different* case — a sign-in whose row could
    not be written — and is deliberately left alone. That one is tolerated by
    design (#148), and signing in again would most likely fail the same way.

    Registered before require_keyword so it still runs on gated requests,
    which return a redirect and stop the chain.
    """
    user = session.get("user")
    if user is not None and "id" not in user:
        app.logger.info("Dropping a pre-#148 session identity; it has no user id")
        session.pop("user", None)


@app.before_request
def require_keyword():
    """Block every page behind the keyword gate until it's been entered."""
    if session.get("access_granted"):
        return None
    # The gate page, static assets, and the Google OAuth handshake must load
    # even before the keyword is entered (the OAuth callback carries no keyword
    # session, and signing in exposes no gated content on its own).
    if request.endpoint in ("gate", "static", "login_google", "auth_google_callback"):
        return None
    return redirect(url_for("gate"))


@app.route("/enter", methods=["GET", "POST"])
def gate():
    """Keyword entry screen shown before any access to the site."""
    if session.get("access_granted"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if (request.form.get("keyword") or "") == ACCESS_KEYWORD:
            session["access_granted"] = True
            return redirect(url_for("index"))
        error = "Incorrect keyword. Please try again."
    return render_template("gate.html", error=error)


@app.route("/login/google")
def login_google():
    """Start the Google OAuth flow (redirects to Google's consent screen)."""
    if not GOOGLE_AUTH_AVAILABLE:
        abort(404)
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    """Google redirects back here. Store the display name/email in the session
    only — nothing is persisted. On any failure, fall back to anonymous."""
    if not GOOGLE_AUTH_AVAILABLE:
        abort(404)
    try:
        token = oauth.google.authorize_access_token()
    except Exception:  # invalid state, user declined, network error, etc.
        app.logger.exception("Google OAuth callback failed")
        return redirect(url_for("index"))
    info = token.get("userinfo") or {}
    user_id, preferred_name = _record_sign_in(info)
    # Persist the signed-in session across browser restarts (30 days, see
    # app.permanent_session_lifetime). Anonymous sessions stay non-permanent.
    session.permanent = True
    session["user"] = {
        # id is None when the row couldn't be written (#148) — every reader
        # must tolerate that, as they already do for anonymous visitors.
        "id": user_id,
        # "there" is a rendering placeholder, never stored as anyone's name.
        "name": _claim(info, "name") or _claim(info, "given_name") or "there",
        "given_name": _claim(info, "given_name"),
        "family_name": _claim(info, "family_name"),
        "preferred_name": preferred_name,
        "email": info.get("email"),
        # Google's own verification of the address, kept so is_admin() (#158)
        # can insist on it. Stored as a strict bool: the claim arrives as a
        # bool from the ID token but as the string "true" from some userinfo
        # responses, and "false" is truthy.
        "email_verified": _email_verified(info),
        "picture": info.get("picture"),
    }
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    """Sign out: drop the session identity and return to anonymous browsing."""
    session.pop("user", None)
    return redirect(url_for("index"))


@app.route("/auth/reset", methods=["POST"])
def auth_reset():
    """Reset Auth (#98): drop the WHOLE session — the gate pass and the
    Google identity — returning this browser to the initial unauthenticated
    state, landing on the gate. Settings files are deliberately untouched:
    signing back in restores the user's preferences. The app's browser-side
    storage (widget state etc.) is cleared by the popup's JS before this
    POST. Reachable only from inside the gate, which is fine — outside it
    there is nothing to reset."""
    session.clear()
    return redirect(url_for("gate"))


@app.context_processor
def inject_auth():
    """Expose the signed-in user (if any) and whether Google sign-in is on."""
    return {
        "current_user": session.get("user"),
        "google_auth_enabled": GOOGLE_AUTH_AVAILABLE,
        # Admin-only UI is then a plain {% if is_admin %} (#158).
        "is_admin": is_admin(),
        # Why the delete-account control is greyed, or None if it isn't (#165).
        "account_delete_refusal": (ADMIN_ACCOUNT_UNDELETABLE if is_admin()
                                   else None),
        # Callables, not values: they answer per card (#162, #176).
        "can_delete_card": can_delete_card,
        "delete_refusal": delete_refusal,
        "can_edit_card": can_edit_card,
        "edit_refusal": edit_refusal,
        "can_move_card": can_move_card,
        "move_refusal": move_refusal,
        # #125: the sign-in dialog's text, kept in one place so the popup and
        # the JSON refusal cannot drift apart.
        "add_sign_in_prompt": ADD_SIGN_IN_PROMPT,
        "can_add_cards": can_add_cards(),
        # #126: a blocked visitor is already signed in, so the dialog must not
        # offer them a sign-in link; the Settings popup names the admin.
        "is_blocked": is_blocked(),
        "blocked_notice": blocked_notice() if is_blocked() else None,
    }


@app.context_processor
def inject_settings():
    """Expose the active settings to every template (issue #86) — the seam the
    Settings UI (#13), dictionary choice (#20) and language switches (#46)
    will read from."""
    return {"settings": current_settings()}


# --- topic icons (#223) -----------------------------------------------------
# A topic's picture is found by **name**, not stored against the row: the file
# is `static/img/topics/<slug of the name>.webp`. That keeps the whole feature
# to a convention plus a directory listing, with no column, no migration and
# nothing to keep in step with the topics table.
#
# Deliberately *not* under a per-section folder. A topic can be moved between
# sections (#215) and renamed as a thing rather than a string (#178), so a path
# that encoded the section it happens to sit in today would go stale the first
# time either happened.
#
# When a topic owns an uploaded image of its own (#185) this becomes the
# fallback rather than the only answer, and the template does not change.

TOPIC_ICON_DIR = Path(app.static_folder) / "img" / "topics"
TOPIC_ICON_SUFFIX = ".webp"


def topic_slug(name):
    """The filename stem a topic's icon would have. '' for a nameless topic."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


GAME_ICON_DIR = Path(app.static_folder) / "img" / "games"


def _icon_slugs(directory):
    """Which slugs have a file in `directory`, listed once per process.

    The cache is the reusable half of #223's `topic_icon()`; what was
    topic-specific is slugging a name somebody typed. A game slug is fixed and
    known at write time, so it needs no slugging — but it wants the same cheap
    lookup, so the listing is keyed by directory rather than copied (#253).
    """
    key = str(directory)
    if key not in _icon_slugs.cache:
        try:
            _icon_slugs.cache[key] = {
                path.stem for path in directory.glob("*" + TOPIC_ICON_SUFFIX)}
        except OSError:
            # Not an error: it means nobody has added icons to this checkout.
            _icon_slugs.cache[key] = set()
    return _icon_slugs.cache[key]


_icon_slugs.cache = {}


def game_icon(slug):
    """The static URL of an activity's icon, or None.

    Unlike topics, the set of activities is closed and known, so every one
    ships with an icon and the None case is a safety net rather than, as it is
    for topics, the normal case.
    """
    if slug and slug in _icon_slugs(GAME_ICON_DIR):
        return url_for("static",
                       filename=f"img/games/{slug}{TOPIC_ICON_SUFFIX}")
    return None


def _topic_icon_slugs():
    """Which slugs actually have a file, listed once per process.

    Cached because icons ship with the code: they change on deploy, and a
    deploy reloads. The cost of getting that wrong is small and one-directional
    — a file added while the app is running is not seen until a reload, which
    is exactly when static assets appear anyway.

    A missing directory is not an error. It means nobody has added icons to
    this checkout, and every tile falls back to the plain colour.
    """
    return _icon_slugs(TOPIC_ICON_DIR)


def topic_icon(name):
    """The static URL of this topic's icon, or **None** when it has none.

    None rather than a placeholder path, so the caller decides: the tile keeps
    its plain background instead of rendering a broken image. Most topics have
    no icon — everything in `Other`, and anything a learner invents by looking a
    word up — so the no-icon case is the common one, not the exception.
    """
    slug = topic_slug(name)
    if slug and slug in _topic_icon_slugs():
        return url_for("static",
                       filename=f"img/topics/{slug}{TOPIC_ICON_SUFFIX}")
    return None


# A filter, so a template asks for it per topic rather than every route having
# to thread a parallel structure through render_template().
app.jinja_env.filters["topic_icon"] = topic_icon
app.jinja_env.filters["game_icon"] = game_icon


def delete_account(user_id, keep_cards=True) -> dict:
    """Delete an account and everything belonging to it (issue #165).

    One implementation, two entry points: the Settings popup calls it for the
    signed-in visitor, and the admin maintenance script calls it with any id.
    Deliberately free of Flask request state so both can.

    The order is the whole design. Files cannot join a database transaction,
    so if something fails midway the account must still be in a state that can
    be retried:

    1. Resolve the cards — the choice the user just made.
    2. Delete the chat transcripts and the settings file.
    3. Delete the users row **last**, once nothing points at it any more.

    Returns what happened, for the confirmation message and the log line.
    """
    result = {"cards": 0, "kept": keep_cards, "logs": False,
              "settings": False, "row": False}

    result["cards"] = resolve_user_cards(user_id, keep_cards)

    log_dir = LOG_DIR / str(user_id)
    if log_dir.is_dir():
        shutil.rmtree(log_dir, ignore_errors=True)
        result["logs"] = not log_dir.exists()

    config = settings_store.config_path(user_id)
    try:
        config.unlink()
        result["settings"] = True
    except FileNotFoundError:
        pass  # nothing saved yet — not a failure

    result["row"] = delete_user(user_id)
    return result


@app.route("/account/delete", methods=["POST"])
def account_delete():
    """Delete my account (issue #165), with the card choice the user made.

    Signed-in visitors only: an anonymous visitor has no account, and a
    sign-in whose users row could not be written (#148) has nothing to delete.
    """
    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"ok": False, "error": SIGN_IN_TO_DELETE_ACCOUNT}), 403
    if is_admin():
        # Enforced here, not only by greying the button: the control is
        # presentation and a hand-made POST goes straight past it (#162).
        return jsonify({"ok": False, "error": ADMIN_ACCOUNT_UNDELETABLE}), 403

    # Anything other than an explicit "delete" keeps the cards. The safer of
    # the two options is the one a malformed request falls back to.
    keep_cards = (request.form.get("cards") or "keep").strip().lower() != "delete"
    try:
        result = delete_account(user_id, keep_cards)
    except Exception:
        app.logger.exception("Account deletion failed for user %s", user_id)
        flash(("Deleting your account failed — nothing was removed. "
               "Please try again.", None))
        return redirect(url_for("index"))

    applog.account_deleted(user_id, cards=result["cards"], kept=result["kept"])
    # The identity only — not the whole session. The keyword gate is about the
    # site, not the account, so a deleted user lands back inside it as an
    # anonymous visitor rather than being asked for the keyword again (which
    # is what Reset Auth is for, #98).
    session.pop("user", None)
    flash((f"Your account was deleted. {result['cards']} card(s) were "
           f"{'kept for other learners' if keep_cards else 'deleted'}.", None))
    return redirect(url_for("index"))


@app.route("/settings", methods=["POST"])
def save_settings():
    """Persist choices from the Settings popup (issues #13/#20) for the
    signed-in user's own config file. Anonymous visitors all share
    config-default.json, so letting one of them write would change the
    settings for every anonymous visitor — the endpoint therefore requires
    a signed-in user (#102); the popup renders read-only for the rest.
    The store drops unknown keys and invalid values, so this endpoint
    cannot corrupt a config file."""
    if not session.get("user"):
        return jsonify({
            "ok": False,
            "error": "Sign in with Google to change settings.",
        }), 403
    changes = request.get_json(silent=True) or {}
    stored = settings_store.update(changes, _current_user_id(),
                                   _current_email())
    # Logged here rather than in settings_store (#161): the store is also read
    # on every request and writes a file when one does not exist yet, so logging
    # from inside it would record a *visit* as a change. This is the one place a
    # person deliberately changes something.
    applog.settings_changed(changes, stored, user=_current_email())
    return jsonify({"ok": True, "settings": stored})


@app.route("/mykola/chat", methods=["POST"])
def mykola_chat():
    """
    Chat endpoint for the Mykola widget. Delegates to the imported MykolaAgent
    (from the ai_agent repo) and returns its {response, sources, history} JSON.
    Behind the keyword gate like every other route.
    """
    return _handle_mykola_chat_request()


@app.route("/api/chat", methods=["POST"])
def mykola_chat_api():
    """Compatibility endpoint used by ai_agent chat page JS."""
    return _handle_mykola_chat_request()


@app.route("/mykola/recap", methods=["POST"])
def mykola_recap():
    """Welcome-back recap of the signed-in user's previous conversations
    (issue ai_agent#30). The recap is an optional nicety: anonymous visitors,
    empty histories, older agent versions, and errors all return
    {"recap": null} so the widget silently keeps its normal greeting."""
    if not MYKOLA_AVAILABLE or not session.get("user") or is_blocked():
        return jsonify({"recap": None})
    # The learner already said goodbye today: wish them a good rest instead
    # of restarting the dialogue (ai_agent#39). Deterministic — no model call.
    if _said_farewell_today():
        name = _current_first_name() or "Dear friend"
        return jsonify({
            "recap": f"{name}, please have a rest, and return tomorrow! Goodnight!"
        })
    agent = get_mykola()
    if not hasattr(agent, "recap"):  # older ai_agent checkout
        return jsonify({"recap": None})
    logs = _read_user_logs()
    if not logs:
        return jsonify({"recap": None})
    try:
        text = agent.recap(logs, **_agent_kwargs(agent.recap))
        return jsonify({"recap": text or None})
    except Exception:
        app.logger.exception("Mykola recap failed")
        return jsonify({"recap": None})


@app.route("/mykola/restart-check", methods=["POST"])
def mykola_restart_check():
    """Should the widget's stale conversation be restarted? (ai_agent#54)

    The widget asks on load, sending the moment of its own last message. A
    break longer than the user's `restart_chat_interval` (hours; 0 = never)
    starts a fresh chat: Mykola reviews the last three exchanges, a new
    chat-log file is opened, and the widget is handed its id and his recap.

    Like the recap endpoint, this is an optional nicety — every failure path
    answers {"restart": false} so the chat simply carries on.
    """
    if not MYKOLA_AVAILABLE:
        return jsonify({"restart": False, "reason": "unavailable"})
    if is_blocked():
        # No conversation to restart — the widget is not there (#126).
        return jsonify({"restart": False, "reason": "blocked"})
    hours = current_settings()["restart_chat_interval"]
    if not hours:
        return jsonify({"restart": False, "reason": "disabled"})

    data = request.get_json(silent=True) or {}
    moments = [m for m in (_last_chat_activity(),
                           _client_last_activity(data.get("last_message_at")))
               if m is not None]
    if not moments:
        return jsonify({"restart": False, "reason": "no history"})
    away_hours = (datetime.now() - max(moments)).total_seconds() / 3600
    if away_hours < hours:
        return jsonify({"restart": False, "away_hours": round(away_hours, 2)})

    recap = _restart_recap(away_hours)
    chat_id = _new_chat_id()
    _start_chat_log(chat_id, away_hours, recap)
    return jsonify({"restart": True, "away_hours": round(away_hours, 2),
                    "chat_id": chat_id, "recap": recap})


def _restart_recap(away_hours: float) -> str | None:
    """Mykola's review of the last three exchanges, opening the restarted
    chat. None whenever it can't be produced — anonymous visitors (no logs),
    an older agent without recap(), or an API failure — in which case the
    fresh chat simply starts from his usual greeting."""
    agent = get_mykola()
    if not hasattr(agent, "recap"):
        return None
    exchanges = _last_exchanges(3)
    if not exchanges:
        return None
    try:
        text = agent.recap(exchanges,
                           **_agent_kwargs(agent.recap, away_hours=away_hours))
        return text or None
    except Exception:
        app.logger.exception("Mykola restart recap failed")
        return None


@app.route("/db/test", methods=["POST"])
def test_db_connection():
    """Is the database reachable? (#184)

    Answers JSON rather than flashing a banner and redirecting, because the
    button moved into the Settings popup, which opens on every page — a
    redirect would drop the visitor onto the index from wherever they were,
    losing a half-typed lookup for the sake of a diagnostic.

    A failure is a normal answer here, not a server error: the caller asked a
    question and gets one, so this stays 200 either way and the popup shows
    the reason.
    """
    try:
        conn = get_db_connection()
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


@app.route("/topics.json")
def topics_json():
    """Topic tile data for the Browse flashcards section — fetched by the
    Mykola widget to refresh the tiles after a card is added from chat
    (issue #53). Same DB-unreachable fallback as the index page.

    Two shapes, and both are load-bearing (#218). `sections` groups the topics
    the way the index page renders them; `topics` is the flat list, still what
    the move dialog offers as suggestions (#177). Adding the first without
    keeping the second would have emptied that dialog's datalist.
    """
    owner = cards_owner_filter()
    try:
        topics = get_topics(owner)
        sections = get_topics_by_section(owner)
    except Exception:
        topics, sections = [], []
    # Icons ride alongside as a name -> URL map rather than as a third element
    # in each pair (#223). The pair is what `get_topics_by_section()` returns and
    # what the move dialog reads; widening it would push a presentation concern
    # into the database layer and into every existing reader.
    icons = {name: topic_icon(name)
             for _, pairs in sections for name, _ in pairs
             if topic_icon(name)}
    return jsonify({"topics": topics, "sections": sections, "icons": icons})


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page: look up a Reverso word or upload a notes file
    (.txt / .docx / .mht, #137).
    Successful submissions save flashcards and redirect to the topic page.
    """
    message = None
    proposed = None
    proposed_topic = None
    source_content = None  # readable text of an upload, shown beside its cards
    duplicate_warning = None  # the word to warn about before looking it up (#145)
    write_refusal = None  # why a write was refused, if one was (#125/#126)
    if request.method == "POST":
        action = request.form.get("action")
        topic = (request.form.get("topic") or "general").strip() or "general"
        try:
            if action == "parse_word":
                word = (request.form.get("word") or "").strip()
                if not word:
                    message = "Please enter a word."
                elif not request.form.get("force_lookup") and _word_already_saved(word):
                    # Early duplicate warning (#145): the word already has
                    # cards, so ask before the (slow) lookup and review dialog.
                    duplicate_warning = word
                    proposed_topic = topic
                else:
                    prefs = current_settings()
                    # The provider-by-provider detail is logged by the parser;
                    # this line carries the identity it cannot see (#30).
                    applog.lookup_started(
                        word, prefs["translator"],
                        prefs["explanatory_dictionary"], user=_current_email())
                    entries = lookup_word(
                        word, topic=topic,
                        translator=prefs["translator"],
                        explanatory_dictionary=prefs["explanatory_dictionary"],
                    )
                    if prefs["cards_automatically"] and not can_add_cards():
                        # #125/#126: nothing may be written, so the automatic
                        # save cannot happen. The lookup already succeeded, so
                        # show its cards in the review popup rather than
                        # throwing the work away — signing in from the prompt
                        # leaves them there to be added.
                        applog.card_add_denied(
                            {"word": word}, source="automatic add",
                            user=_current_email(),
                            reason="blocked" if is_blocked() else "anonymous")
                        proposed = entries
                        proposed_topic = topic
                        write_refusal = add_refusal()
                    elif prefs["cards_automatically"]:
                        # 'Add cards automatically' is on (#13): skip the
                        # review popup, write the cards straight to the DB.
                        # Duplicates are skipped and reported (issue #101).
                        added = sum(
                            1 for entry in entries
                            if _save_and_log(entry, source="automatic add")
                        )
                        skipped = len(entries) - added
                        if not added:
                            note = duplicate_notice(entries)   # #186
                            flash((f"All {skipped} card(s) for '{word}' are "
                                   "already in the database — nothing added."
                                   + (f" {note}" if note else ""), None))
                        elif skipped:
                            flash((f"Added {added} card(s) for '{word}' "
                                   f"automatically, skipped {skipped} already "
                                   "in the database.", topic))
                        else:
                            flash((f"Added {added} card(s) for '{word}' automatically.", topic))
                        return redirect(url_for("index"))
                    # Don't save yet: show the cards for review/editing first.
                    proposed = entries
                    proposed_topic = topic

            elif action == "upload_notes":
                # Parsing costs money before it costs anything else (#200):
                # a Reverso .mht/.docx sends its glued translations to Claude
                # (_split_glued_translations), and that happens *before* the
                # save this visitor may not be allowed to make. Ask the same
                # guard the save routes ask, at the door — after this point the
                # money is spent on cards #125 would refuse to store.
                #
                # Every upload, not only the expensive kinds: which file calls
                # Claude cannot be known without parsing it, and parsing is the
                # thing being paid for.
                write_refusal = add_refusal()
                file = request.files.get("notes_file")
                if write_refusal:
                    pass          # refused: the file is not even read
                elif file is None or not file.filename:
                    message = "Please choose a .txt, .docx or .mht file."
                else:
                    # Don't save yet: show the parsed cards next to the file
                    # content for review/editing, like the word lookup does.
                    # The parser is picked by extension (#137).
                    data = file.read()
                    try:
                        with applog.Timer() as timer:
                            entries, source_content = parse_notes_preview(
                                file.filename, data, topic=topic)
                    except Exception as e:
                        applog.file_rejected(file.filename, e,
                                             user=_current_email())
                        raise
                    applog.file_parsed(file.filename, len(data), len(entries),
                                       topic=topic, user=_current_email(),
                                       elapsed_ms=timer.ms)
                    if not entries:
                        message = "No vocabulary entries found in that file."
                    else:
                        proposed = entries
                        proposed_topic = topic

        except Exception as e:
            message = f"Error: {e}"

    try:
        sections = get_topics_by_section(cards_owner_filter())
    except Exception:
        sections = []  # DB unreachable (e.g. locally) — page still works

    return render_template(
        "index.html", message=message, sections=sections,
        proposed=proposed, proposed_topic=proposed_topic,
        source_content=source_content, duplicate_warning=duplicate_warning,
        write_refusal=write_refusal,
    )


@app.route("/cards/add", methods=["POST"])
def add_card():
    """
    Save one reviewed (possibly edited) card from the lookup popup.
    Returns JSON so the popup can stay open for the remaining cards.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    def json_list(field):
        # Examples travel as a hidden JSON array (e.g. from the Reverso parser,
        # issue #134), so they survive the review popup instead of being lost.
        raw = (request.form.get(field) or "").strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        items = [str(x).strip() for x in value if str(x).strip()] \
            if isinstance(value, list) else []
        return items or None

    word = cleaned("word")
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    entry = {
        "word": word,
        "pos": cleaned("pos"),
        "topic": cleaned("topic") or "general",
        "explanation_en": cleaned("explanation_en"),
        "examples_en": json_list("examples_en"),
        "translation_ukr": cleaned("translation_ukr"),
        "examples_ukr": json_list("examples_ukr"),
        "translation_rus": cleaned("translation_rus"),
        "examples_rus": json_list("examples_rus"),
    }
    refusal = add_refusal()
    if refusal:
        # #125/#126. Answered here rather than by hiding the button: these
        # forms are ordinary POSTs and a hand-made one goes straight past the
        # UI. `sign_in_required` is what tells the popup to show the message
        # instead of its generic "saving failed" alert.
        applog.card_add_denied(entry, source="review popup",
                               user=_current_email(),
                               reason="blocked" if is_blocked() else "anonymous")
        return {"ok": False, "sign_in_required": True, "error": refusal}, 403
    if not _save_and_log(entry, source="review popup"):
        # #101 skipped it; #186 explains when the blocking card is hidden.
        # The key is present only when there is something extra to say, so the
        # ordinary duplicate answer keeps its existing shape.
        body = {"ok": True, "saved": False, "duplicate": True}
        note = duplicate_notice([entry])
        if note:
            body["note"] = note
        return body
    return {"ok": True, "saved": True}


@app.route("/flashcards/<topic>")
def flashcards(topic):
    """Display all flashcards saved under the given topic."""
    cards = get_flashcards_by_topic(topic, cards_owner_filter())
    # The move dialog's topic suggestions (#177) are fetched from
    # /topics.json when it first opens, rather than queried here: this page is
    # loaded by everyone and the list is only needed by someone who actually
    # moves a card.
    return render_template("flashcards.html", topic=topic, cards=cards)


# A tiny sample deck so the card-deck activity (#78) can be opened and its
# flip animation previewed when the database is unreachable (e.g. local dev,
# where PythonAnywhere MySQL is not accessible).
# TODO(#78): remove this fallback once the deck can be exercised against a real
#   DB locally (fixtures / seeded local MySQL). It exists only for the demo.
_DEMO_DECK = [
    {"word": "streamline", "pos": "verb",
     "explanation_en": "to make a system or process work more simply and effectively",
     "translation_ukr": "оптимізувати", "translation_rus": "оптимизировать"},
    {"word": "resilient", "pos": "adjective",
     "explanation_en": "able to recover quickly from difficult conditions",
     "translation_ukr": "стійкий", "translation_rus": "устойчивый"},
    {"word": "insight", "pos": "noun",
     "explanation_en": "a clear, deep understanding of a complicated situation",
     "translation_ukr": "розуміння", "translation_rus": "понимание"},
]


def _deck_translation(prefs):
    """Which translation the deck shows, following the visibility settings
    (#46/#79/#111): Ukrainian when visible, else Russian; both hidden -> none.
    Per #78, when both languages are visible Ukrainian wins."""
    if prefs["show_ukrainian"]:
        return "translation_ukr", "Ukrainian"
    if prefs["show_russian"]:
        return "translation_rus", "Russian"
    return None, None


@app.route("/deck/<topic>")
def card_deck(topic):
    """Flashcards activity (#78): a browsable deck of flip cards for one topic.

    One card shows at a time — its word on the front; flipping reveals the
    explanation plus one translation. Left/Right arrows step through the deck.
    The flip animation is scoped to this page's template, so it stays local to
    this activity and doesn't affect the rest of the app.
    """
    prefs = current_settings()
    try:
        cards = get_flashcards_by_topic(topic, cards_owner_filter())
        demo = False
    except Exception:
        # DB unreachable — fall back to the sample deck so the activity still
        # renders (see _DEMO_DECK). TODO(#78): drop this branch with a real DB.
        cards = _DEMO_DECK
        demo = True
    trans_field, trans_label = _deck_translation(prefs)
    return render_template(
        "cards.html", topic=topic, cards=cards,
        trans_field=trans_field, trans_label=trans_label, demo=demo,
    )


@app.route("/flashcards/<topic>/delete/<int:card_id>", methods=["POST"])
def delete_card(topic, card_id):
    """Delete one flashcard, if this visitor may (#162), and return to the topic.

    Enforced here rather than in the template: greying the cross is
    presentation, and a hand-made POST goes straight past it. Until this
    landed the route had no identity check at all, so anyone past the keyword
    gate could delete any card.
    """
    if is_blocked():
        # #126: a blocked account keeps its cards but may not remove them,
        # exactly as it may not add any. Checked before ownership, so the
        # answer does not depend on whose card it is.
        applog.card_delete_denied(card_id, topic=topic, user=_current_email(),
                                  reason="blocked")
        flash((blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))

    user_id = _current_user_id()
    admin = is_admin()
    if not admin and user_id is None:
        # No identity at all — an anonymous visitor, or a sign-in whose users
        # row could not be written (#148). Nothing can be theirs, so this is
        # #125's sign-in prompt rather than #162's "someone else's card".
        applog.card_delete_denied(card_id, topic=topic, user=_current_email(),
                                  reason="anonymous")
        flash((DELETE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))

    word, outcome = delete_flashcard(card_id, owner_id=user_id, admin=admin)
    if outcome == "deleted":
        applog.card_deleted(card_id, word, topic=topic, user=_current_email())
        flash((f"Deleted card '{word}'.", None))
    elif outcome == "denied":
        applog.card_delete_denied(card_id, topic=topic,
                                  user=_current_email(), reason="not owner")
        flash((DELETE_NOT_YOURS, None))
    else:
        applog.card_delete_missed(card_id, topic=topic, user=_current_email())
        flash(("Card not found — it may have already been deleted.", None))
    return redirect(url_for("flashcards", topic=topic))


@app.route("/flashcards/<topic>/move/<int:card_id>", methods=["POST"])
def move_card(topic, card_id):
    """Move one card to another topic (#177), then go somewhere sensible.

    A redirect with a flash rather than JSON, unlike editing: the card leaves
    the page it was moved from, so there is nothing to re-render in place and
    the useful feedback is a sentence naming where it went.
    """
    to_topic = (request.form.get("to_topic") or "").strip()

    if is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="blocked")
        flash((blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))
    user_id = _current_user_id()
    admin = is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="anonymous")
        flash((MOVE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))
    if not to_topic:
        flash(("Choose a topic to move the card to.", None))
        return redirect(url_for("flashcards", topic=topic))

    outcome, detail = move_flashcard(card_id, to_topic, owner_id=user_id,
                                     admin=admin)
    if outcome == "denied":
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="not owner")
        flash((MOVE_NOT_YOURS, None))
        return redirect(url_for("flashcards", topic=topic))
    if outcome == "missing":
        flash(("Card not found — it may have already been deleted.", None))
        return redirect(url_for("flashcards", topic=topic))
    if outcome == "unchanged":
        flash((f"That card is already in '{topic}'.", None))
        return redirect(url_for("flashcards", topic=topic))

    word, from_topic = detail
    # Both ends of the move (#161). `from_topic` was already being unpacked here
    # and then dropped, so the log could say where a card had landed but never
    # where it came from — the one thing a move is actually about.
    applog.card_moved(card_id, word, from_topic, to_topic,
                      user=_current_email())
    flash((f"Moved '{word}' to '{to_topic}'.", to_topic))

    # Moving the last card out of a topic makes that topic cease to exist —
    # there is no topics table. Landing back on a page that no longer has
    # anything to show, for a topic that has vanished from the chips, reads as
    # a bug; the topic list is the honest destination.
    try:
        remaining = [name for name, _ in get_topics(cards_owner_filter())]
    except Exception:
        remaining = [from_topic]      # DB unreachable: stay put rather than guess
    if from_topic not in remaining:
        return redirect(url_for("index"))
    return redirect(url_for("flashcards", topic=from_topic))


@app.route("/flashcards/<topic>/edit/<int:card_id>", methods=["POST"])
def edit_card(topic, card_id):
    """Change a saved card's content (#176). JSON, so the dialog can stay open
    and show a refusal in place rather than losing what was typed.

    Enforced here rather than in the template for the same reason as #162:
    greying the pencil is presentation, and a hand-made POST goes past it.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    def json_list(field):
        raw = (request.form.get(field) or "").strip()
        if not raw:
            return None
        # Examples arrive either as the hidden JSON the review popup uses
        # (#134) or as one-per-line text from the edit dialog's textarea.
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                value = None
            if isinstance(value, list):
                items = [str(x).strip() for x in value if str(x).strip()]
                return items or None
        items = [line.strip() for line in raw.splitlines() if line.strip()]
        return items or None

    if is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="blocked")
        return {"ok": False, "error": blocked_notice()}, 403
    user_id = _current_user_id()
    admin = is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="anonymous")
        return {"ok": False, "error": EDIT_SIGN_IN_PROMPT}, 403

    word = cleaned("word")
    if not word:
        return {"ok": False, "error": "word is required"}, 400

    # Only what was actually submitted: a field the dialog did not render —
    # a language this visitor has hidden (#46/#79/#111) — must be left alone,
    # not blanked. `update_flashcard` reads a missing key as "don't touch".
    readers = {
        "word": lambda: word,
        "pos": cleaned,
        "explanation_en": cleaned,
        "translation_ukr": cleaned,
        "translation_rus": cleaned,
        "examples_en": json_list,
        "examples_ukr": json_list,
        "examples_rus": json_list,
    }
    entry = {field: (read() if field == "word" else read(field))
             for field, read in readers.items() if field in request.form}

    outcome, detail = update_flashcard(card_id, entry, owner_id=user_id,
                                       admin=admin)
    if outcome == "updated":
        applog.card_edited(entry, source="card page", user=_current_email(),
                           card_id=card_id, changed=detail)
        return {"ok": True, "changed": detail}
    if outcome == "unchanged":
        return {"ok": True, "changed": []}
    if outcome == "duplicate":
        _, dup_word, dup_pos = detail
        named = f"'{dup_word}'" + (f" ({dup_pos})" if dup_pos else "")
        return {"ok": False, "error": (
            f"Another card for {named} already exists, so this one cannot be "
            "renamed to it.")}, 409
    if outcome == "denied":
        applog.card_edit_denied(card_id, topic=topic, user=_current_email(),
                                reason="not owner")
        return {"ok": False, "error": EDIT_NOT_YOURS}, 403
    return {"ok": False, "error": "Card not found — it may have been deleted."}, 404


def _answer_variants(translation):
    """
    Split a stored translation like 'дом, здание, жильё' into normalized
    variants accepted as correct answers.
    """
    return {
        variant.strip().lower().replace("ё", "е")
        for variant in translation.split(",")
        if variant.strip()
    }


QUIZ_LANGS = {"rus": "Russian", "ukr": "Ukrainian"}

# Quiz language code -> the settings key that controls its visibility
# (#46/#79/#111). A hidden language can't be quizzed on.
QUIZ_LANG_SETTINGS = {"rus": "show_russian", "ukr": "show_ukrainian"}

# quiz_lang setting value (#113) -> quiz language code.
QUIZ_LANG_CODES = {"ukrainian": "ukr", "russian": "rus"}


def _visible_quiz_langs(prefs):
    """The QUIZ_LANGS subset this identity hasn't hidden in Settings."""
    return {
        code: name for code, name in QUIZ_LANGS.items()
        if prefs[QUIZ_LANG_SETTINGS[code]]
    }


def _visible_sections():
    """`get_topics_by_section()` for this visitor, or [] if the DB is down.

    Same tolerance the index page has: a dead database leaves the picker with
    nothing to offer rather than a 500.
    """
    try:
        return get_topics_by_section(cards_owner_filter())
    except Exception:
        app.logger.exception("Could not list topics for the picker")
        return []


def _render_picker(activity, start_url):
    """The topic picker (#250), shared by every activity in #233.

    `start_url` is where the form submits. It carries no topics of its own —
    the ticked boxes are the query string, which is why this is a plain GET
    form: the resulting URL is the shareable, bookmarkable one #233 asked for,
    and no JavaScript is needed to build it.

    The remembered selection (#248) is re-checked against what is visible now,
    so a topic deleted, renamed, or hidden by #127 since the last round simply
    is not ticked.
    """
    sections = _visible_sections()
    visible = games.visible_topic_names(sections)
    # The translation language, chosen before the words are drawn rather than
    # after (#113). Offered only when there is a choice to make: with one
    # language hidden in Settings (#46/#79) a lone radio is a control that
    # cannot do anything, and the round says which language it is using anyway.
    quiz_langs = {}
    quiz_lang = None
    if activity.picks_language:
        prefs = current_settings()
        visible_langs = _visible_quiz_langs(prefs)
        if len(visible_langs) > 1:
            quiz_langs = visible_langs
            quiz_lang = _quiz_lang(prefs, visible_langs)
    return render_template(
        "picker.html",
        activity=activity,
        start_url=start_url,
        quiz_langs=quiz_langs,
        quiz_lang=quiz_lang,
        sections=[(name, topics) for name, topics in sections if topics],
        selected=set(games.remembered_selection(session, visible)),
        total_cards=sum(count for _, topics in sections for _, count in topics),
        words=games.remembered_word_count(session),
        words_min=games.QUIZ_WORDS_MIN,
        words_max=games.QUIZ_WORDS_MAX,
    )


def activity_picker_url(activity):
    """Where an activity's tile points: its picker, never a round (#233).

    The quiz keeps its own URLs, so the fork lives here rather than in each
    template — the panel exists to hide exactly this seam.
    """
    if activity.kind == "quiz":
        return url_for("quiz_topics")
    return url_for("game_picker", game=activity.slug)


def activity_play_url(activity, topic):
    """Straight into an activity for one topic — the topic page's links (#253).

    No picker: the topic is already chosen, and that is the whole context the
    page is in. A topic too thin for the activity is explained *there*, which
    is why this never redirects.
    """
    if activity.kind == "quiz":
        return url_for("quiz", topic=topic)
    return url_for("game_play", game=activity.slug, topic=topic)


@app.context_processor
def inject_activities():
    """The one declaration, reachable from every template that renders it
    (#233): the front-page panels and the topic page's activity row."""
    return {
        "game_activities": games.panel("game"),
        "quiz_activity": games.ACTIVITIES["quiz"],
        "reader_activity": games.ACTIVITIES["read_a_text"],
        "generation_available": _generation_available(),
        "activity_picker_url": activity_picker_url,
        "activity_play_url": activity_play_url,
    }


@app.route("/games/<game>")
def game_picker(game):
    """The picker for one game (#250).

    Every game slug 404s until its own ticket registers it in
    `games.ACTIVITIES` and adds a /games/<slug>/play route. That is deliberate:
    a tile that opened a picker whose start button led nowhere would be worse
    than no tile at all.
    """
    activity = games.activity(game, kind=games.GAMES_URL_KINDS)
    if activity is None:
        abort(404)
    return _render_picker(
        activity, url_for("game_play", game=activity.slug))


def _generation_available():
    """Whether text generation can run — #237's "no key, no panel" (#253).

    Read at **request time**, never at import. `ANTHROPIC_API_KEY` reaches this
    process only as a side effect of importing `ai_agent`, which loads its own
    `.env` (see AI_AGENT_PATH above), and that import happens after this
    module's constants would have been evaluated. A module-level constant would
    therefore read None on a perfectly working deployment.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _round_stub(activity, topics):
    """The round an activity will have, before it has one (#253).

    Every activity registers now, so its tile, its picker and its Start button
    are all real. Only the round is missing, and this says so — naming the
    ticket that owns it and the selection it would have played, with the picker
    a click away. A tile whose Start led to a 404 would be worse than no tile.
    """
    # Truncated, because "no ?topic=" resolves to *every* visible topic
    # (#248), and naming all twenty-six of them is a wall of text where one
    # line was wanted. Same three-then-ellipsis rule the quiz's title uses.
    shown = ", ".join(topics[:NAMED_TOPICS])
    if len(topics) > NAMED_TOPICS:
        shown += " …"
    return render_template("game_stub.html", activity=activity,
                           topics=topics, topic_summary=shown)


# slug -> the view that renders one round, as `f(activity, topics)`. Every
# registered activity has an entry; a game ticket replaces its stub with the
# real round and touches nothing else.
GAME_ROUNDS = {activity.slug: _round_stub
               for activity in games.ACTIVITIES.values()
               if activity.kind in games.GAMES_URL_KINDS}


@app.route("/games/<game>/play")
def game_play(game):
    """A round of one game over the selected topics (#250).

    The selection is resolved here rather than in each game, so every game
    inherits #233's rules for free: page order, topics that have since gone
    dropped in silence, and **no `topic` parameter meaning the whole visible
    deck** — which is what keeps a bare link to this URL, the kind the topic
    page's activity row will build, meaningful.

    Playing also remembers the selection, so the picker opens on it next time.
    """
    activity = games.activity(game, kind=games.GAMES_URL_KINDS)
    round_view = GAME_ROUNDS.get(game)
    if activity is None or round_view is None:
        abort(404)
    topics = games.resolve_selection(
        request.args.getlist("topic"),
        games.visible_topic_names(_visible_sections()))
    games.remember_selection(session, topics)
    return round_view(activity, topics)


def _quiz_lang(prefs, langs):
    """Which language this quiz runs in (#113).

    Without an explicit ?lang=, the identity's preference; the in-page switch
    still overrides it, and a preference for a language hidden in Settings
    (#46/#79) falls back to a visible one.
    """
    default_lang = QUIZ_LANG_CODES.get(prefs["quiz_lang"], "ukr")
    lang = request.args.get("lang") or default_lang
    if lang not in langs:
        lang = default_lang if default_lang in langs else next(iter(langs))
    return lang


# How many of a selection are named under a quiz's title before the rest
# become an ellipsis. Three fits one line on a phone, which is the constraint;
# the count in the title is what answers "how many" exactly.
NAMED_TOPICS = 3


def _topic_summary(topics):
    """The topic names to print under a quiz's title, or **None**.

    None for a single topic, because the title already names it — "Quiz: Work"
    above a line reading "Work" is the same word twice. The title of a several-
    topic run says how many, which is the one thing this line cannot: it is
    truncated, and a truncated list that also had to be countable would have to
    show every name.
    """
    if len(topics) < 2:
        return None
    shown = ", ".join(topics[:NAMED_TOPICS])
    return f"{shown} …" if len(topics) > NAMED_TOPICS else shown


def _run_quiz(topics, heading, self_url, back, words):
    """Render or grade a quiz over `topics` — one of them or several (#250).

    `self_url(lang=...)` builds this quiz's own URL, because the language
    switch, the form action and "Try again" all need it and only the caller
    knows which of the two route shapes it is. `back` is the crumb link, which
    is the topic page for one topic and the picker for a selection.

    Everything about the quiz itself is unchanged (#233 asked for exactly one
    change): typed answers, the same grading, `quiz_lang`, and skipping cards
    with no translation in the chosen language. Several topics grade as one run
    because they are one list of cards by the time they get here.
    """
    prefs = current_settings()
    langs = _visible_quiz_langs(prefs)
    common = {"heading": heading, "self_url": self_url, "back": back,
              "langs": langs, "topic_summary": _topic_summary(topics)}
    if not langs:
        # Both languages hidden in Settings (#46/#79) — nothing to quiz on.
        return render_template(
            "quiz.html", cards=[], lang=None, lang_name=None,
            results=None, **dict(common, langs={}))

    lang = _quiz_lang(prefs, langs)
    field = f"translation_{lang}"
    # A card with no translation in this language cannot be asked, so it is
    # dropped *before* the draw — the sample can only contain answerable words.
    # How many were dropped is worth saying, though: the picker counts cards,
    # not cards with a Ukrainian translation, so a learner who ticked 41 and
    # was asked 20 has no way to tell that from the word limit. 74 of the 569
    # cards in production have no Ukrainian and 38 no Russian, so this is a
    # number people will actually meet.
    in_selection = get_flashcards_by_topics(topics, cards_owner_filter())
    cards = [c for c in in_selection if c.get(field)]
    untranslated = len(in_selection) - len(cards)

    results = score = None
    if request.method == "POST":
        # Grade the questions that were **asked**, not a fresh draw. The round
        # is a random sample, so re-sampling here would mark answers against
        # words the learner never saw. The submitted field names say which
        # cards were on the page, and they are the only record of it — which is
        # also why this needs no server-side state between the two requests.
        # In the order they were **asked**, which is the order the fields were
        # submitted — not the order the database returned them. The results
        # list is numbered, and a learner reading "3. wrong" has to find the
        # third question they answered, not the third alphabetically.
        by_id = {str(card["id"]): card for card in cards}
        cards = []
        for key in request.form:
            if not key.startswith("answer_"):
                continue
            card = by_id.pop(key[len("answer_"):], None)
            if card is not None:      # pop, so a repeated field cannot
                cards.append(card)    # ask the same question twice
        results = []
        for card in cards:
            user_answer = (request.form.get(f"answer_{card['id']}") or "").strip()
            normalized = user_answer.lower().replace("ё", "е")
            correct = normalized in _answer_variants(card[field])
            results.append({
                "word": card["word"],
                "pos": card.get("pos"),
                "user_answer": user_answer,
                "expected": card[field],
                "correct": correct,
            })
        score = sum(1 for r in results if r["correct"])
    else:
        # A round is `words` questions drawn uniformly from every card in the
        # selection — so a topic with 36 cards contributes more of them than
        # one with 20, which is what drawing from the words rather than from
        # the topics means. Fewer cards than asked for is simply a shorter
        # round.
        cards = games.sample(cards, words)

    return render_template(
        "quiz.html", cards=cards, lang=lang, lang_name=QUIZ_LANGS[lang],
        results=results, score=score, untranslated=untranslated, **common)


@app.route("/quiz/<topic>", methods=["GET", "POST"])
def quiz(topic):
    """Quiz on one topic — the original route, unchanged and still linked.

    Three templates build `url_for('quiz', topic=...)`, and #233 requires that
    none of them break. Which is why the several-topic quiz is a **separate
    endpoint** rather than a second rule on this one: with both shapes on one
    endpoint, `url_for` has to choose between the path converter and a repeated
    query parameter, and it picks the converter — making the multi-topic URL
    unbuildable.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    return _run_quiz(
        [topic],
        heading=topic,
        self_url=lambda lang: url_for("quiz", topic=topic, lang=lang,
                                      words=words),
        back=(url_for("flashcards", topic=topic), f"Flashcards: {topic}"),
        words=words,
    )


@app.route("/quiz", methods=["GET", "POST"])
def quiz_topics():
    """The picker, or a quiz over the topics it submitted (#250).

    One URL doing both, because #233 specified `/quiz` for the picker and
    `/quiz?topic=A&topic=B` for the run. **No `topic` parameter means the
    picker**, which is the one place this differs from a game: a game's round
    has its own `/play` URL, so there an absent parameter can safely mean the
    whole deck. Here the two would be the same URL, and a picker nobody can
    reach is worse than a shortcut nobody has asked for — ticking "Select all"
    is the way to quiz on everything.
    """
    requested = request.args.getlist("topic")
    if not requested:
        return _render_picker(
            games.ACTIVITIES["quiz"], url_for("quiz_topics"))

    topics = games.resolve_selection(
        requested, games.visible_topic_names(_visible_sections()))
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    games.remember_selection(session, topics)
    games.remember_word_count(session, words)
    heading = topics[0] if len(topics) == 1 else f"{len(topics)} topics"
    return _run_quiz(
        topics,
        heading=heading,
        self_url=lambda lang: url_for("quiz_topics", topic=topics, lang=lang,
                                      words=words),
        back=(url_for("quiz_topics"), "Choose topics"),
        words=words,
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
