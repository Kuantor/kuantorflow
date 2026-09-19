import inspect
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from jinja2 import ChoiceLoader, Environment, FileSystemLoader

import applog
import games
import topicgen
import settings_store
import utils
import parsers
from parsers import lookup_word, parse_notes_preview
from utils import (
    claim_anonymous_message,
    claim_text_generation,
    claim_word_lookup,
    claim_word_lookups,
    existing_words,
    lookups_used_today,
    delete_flashcard,
    delete_user,
    duplicate_topic,
    fill_missing_fields,
    resolve_user_cards,
    flashcard_word_exists,
    get_db_connection,
    get_flashcards_by_topic,
    get_flashcards_by_topics,
    get_topics,
    get_topics_by_section,
    get_user_block,
    find_duplicate,
    find_saved_words,
    confirmed_words,
    private_topics,
    remember_confirmed_word,
    resolve_topic,
    set_topic_visibility,
    move_flashcard,
    set_preferred_name,
    update_flashcard,
    save_flashcard,
    upsert_user,
)

# The Flask object, its configuration, and the identity and permission
# helpers now live in `web.py` (#418). Imported rather than defined, so a
# feature module can take `app` and `is_admin()` from there without
# importing this module and closing a loop.
#
# `import web` is what the routes below actually call through --
# `web.viewer()`, never the copy bound here -- so one patch on
# `web.viewer` reaches this module and every module still to be written
# (#436). The names are bound here as well, and only for that: a handful
# of tests call `app_module.is_admin()` directly rather than patch it,
# and keeping both spellings valid costs nothing.
import web
from web import (
    app,
    _bool_env,
    ACCESS_KEYWORD,
    _admin_emails,
    ADMIN_EMAILS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_AUTH_AVAILABLE,
    _int_env,
    oauth,
    MAX_MYKOLA_REQUEST_BYTES,
    ANONYMOUS_MESSAGE_LIMIT,
    ANONYMOUS_DAILY_LIMIT,
    LOG_DIR,
    APP_BOOT_ID,
    _current_email,
    DELETE_NOT_YOURS,
    DELETE_SIGN_IN_PROMPT,
    EDIT_NOT_YOURS,
    EDIT_SIGN_IN_PROMPT,
    MOVE_NOT_YOURS,
    MOVE_SIGN_IN_PROMPT,
    ADD_SIGN_IN_PROMPT,
    SIGN_IN_TO_DELETE_ACCOUNT,
    ADMIN_ACCOUNT_UNDELETABLE,
    current_block,
    is_blocked,
    blocked_notice,
    can_add_cards,
    add_refusal,
    can_delete_card,
    _card_refusal,
    can_move_card,
    move_refusal,
    can_edit_card,
    edit_refusal,
    delete_refusal,
    is_admin,
    _current_user_id,
    _identity_token,
    cards_owner_filter,
    viewer,
    current_settings,
    _sections_for_visitor,
    _private_marks,
    GENERATED_COUNT_KEY,
    GENERATION_ANON_LIMIT,
    GENERATION_BUSY_PROMPT,
    GENERATION_DAILY_LIMIT,
    GENERATION_SIGN_IN_PROMPT,
    GENERATION_USER_DAILY,
    GENERATION_USER_LIMIT_PROMPT,
    LOOKED_UP_COUNT_KEY,
    LOOKUP_ANON_DAILY,
    LOOKUP_ANON_LIMIT,
    LOOKUP_BUSY_PROMPT,
    LOOKUP_SIGN_IN_PROMPT,
    LOOKUP_USER_DAILY,
    LOOKUP_USER_LIMIT_PROMPT,
    _generation_available,
    _generation_refusal,
    _lookup_refusal,
)


def _record_sign_in(info):
    """Persist the signed-in identity (#148); return (user_id, preferred_name).

    Returns (None, None) if the row can't be written — an unreachable database
    must not cost the user their login, the same way utils.get_topics() and
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
        return utils.upsert_user(
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


@app.context_processor
def inject_attribution():
    """What a card needs to credit its explanation (#390).

    Three values rather than a rendered string, because the credit is a
    sentence with two links in it and building HTML in Python is how a template
    stops being the place the page is written. `_source_credit.html` is the
    single copy; this is what it reads.
    """
    return {
        "wiktionary_link": parsers.wiktionary_link,
        "wiktionary_licence": parsers.WIKTIONARY_LICENCE,
        "wiktionary_licence_url": parsers.WIKTIONARY_LICENCE_URL,
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
        if (request.form.get("keyword") or "") == web.ACCESS_KEYWORD:
            session["access_granted"] = True
            return redirect(url_for("index"))
        error = "Incorrect keyword. Please try again."
    return render_template("gate.html", error=error)


@app.route("/login/google")
def login_google():
    """Start the Google OAuth flow (redirects to Google's consent screen)."""
    if not web.GOOGLE_AUTH_AVAILABLE:
        abort(404)
    redirect_uri = url_for("auth_google_callback", _external=True)
    return web.oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    """Google redirects back here. Store the display name/email in the session
    only — nothing is persisted. On any failure, fall back to anonymous."""
    if not web.GOOGLE_AUTH_AVAILABLE:
        abort(404)
    try:
        token = web.oauth.google.authorize_access_token()
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
        "google_auth_enabled": web.GOOGLE_AUTH_AVAILABLE,
        # Admin-only UI is then a plain {% if is_admin %} (#158).
        "is_admin": web.is_admin(),
        # Why the delete-account control is greyed, or None if it isn't (#165).
        "account_delete_refusal": (web.ADMIN_ACCOUNT_UNDELETABLE if web.is_admin()
                                   else None),
        # Callables, not values: they answer per card (#162, #176).
        "can_delete_card": web.can_delete_card,
        "delete_refusal": web.delete_refusal,
        "can_edit_card": web.can_edit_card,
        "edit_refusal": web.edit_refusal,
        "can_move_card": web.can_move_card,
        "move_refusal": web.move_refusal,
        # #125: the sign-in dialog's text, kept in one place so the popup and
        # the JSON refusal cannot drift apart.
        "add_sign_in_prompt": web.ADD_SIGN_IN_PROMPT,
        "can_add_cards": web.can_add_cards(),
        # #126: a blocked visitor is already signed in, so the dialog must not
        # offer them a sign-in link; the Settings popup names the admin.
        "is_blocked": web.is_blocked(),
        "blocked_notice": web.blocked_notice() if web.is_blocked() else None,
    }


@app.context_processor
def inject_settings():
    """Expose the active settings to every template (issue #86) — the seam the
    Settings UI (#13), dictionary choice (#20) and language switches (#46)
    will read from."""
    active = web.current_settings()
    return {
        "settings": active,
        # The bounds the Settings popup puts on #235's round-length box, read
        # from the store that validates it rather than written into the markup
        # — the box and sanitize() must agree, and one source is how.
        "gapped_deck_range": settings_store.RANGES["gapped_deck_size"],
        "speech_rate_range": settings_store.RANGES["speech_rate"],
        # The translators this deployment is actually configured for (#353).
        # Read here rather than written into the markup, for the same reason
        # the ranges above are: the panel and the dispatch must agree, and one
        # source is how. Empty is a real state — no key, no translator — and
        # the panel says so rather than showing an empty box.
        "translators": parsers.available_translators(),
        # Every provider, so the panel can show the ones this deployment
        # cannot use yet rather than hiding them (#353). #261's rule for an
        # unfinished game tile: greyed and labelled beats absent, because
        # absent looks like the feature does not exist.
        "all_translators": parsers.TRANSLATORS,
        # The same pair for the dictionaries (#365). Oxford needs no
        # key, so `dictionaries` is never empty -- which is why the
        # panel has no "none configured" branch on this half.
        "dictionaries": parsers.available_dictionaries(),
        "all_dictionaries": parsers.DICTIONARIES,
        # Who a lookup will really ask, resolved by the same functions the
        # dispatch uses (#384). Not the stored slugs: a choice this deployment
        # cannot serve falls back, deliberately and without touching the
        # setting (#365), so a heading that read the preference would name a
        # provider that is never contacted. Either may be None -- no translator
        # is a real state (#348/#349), and the heading says so by naming only
        # the dictionary.
        "lookup_providers": (
            parsers.resolved_translator(active["translator"]),
            parsers.resolved_dictionary(active["explanatory_dictionary"]),
        ),
    }


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

    result["cards"] = utils.resolve_user_cards(user_id, keep_cards)

    log_dir = web.LOG_DIR / str(user_id)
    if log_dir.is_dir():
        shutil.rmtree(log_dir, ignore_errors=True)
        result["logs"] = not log_dir.exists()

    config = settings_store.config_path(user_id)
    try:
        config.unlink()
        result["settings"] = True
    except FileNotFoundError:
        pass  # nothing saved yet — not a failure

    result["row"] = utils.delete_user(user_id)
    return result


@app.route("/account/delete", methods=["POST"])
def account_delete():
    """Delete my account (issue #165), with the card choice the user made.

    Signed-in visitors only: an anonymous visitor has no account, and a
    sign-in whose users row could not be written (#148) has nothing to delete.
    """
    user_id = web._current_user_id()
    if user_id is None:
        return jsonify({"ok": False, "error": web.SIGN_IN_TO_DELETE_ACCOUNT}), 403
    if web.is_admin():
        # Enforced here, not only by greying the button: the control is
        # presentation and a hand-made POST goes straight past it (#162).
        return jsonify({"ok": False, "error": web.ADMIN_ACCOUNT_UNDELETABLE}), 403

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
    stored = settings_store.update(changes, web._current_user_id(),
                                   web._current_email())
    # Logged here rather than in settings_store (#161): the store is also read
    # on every request and writes a file when one does not exist yet, so logging
    # from inside it would record a *visit* as a change. This is the one place a
    # person deliberately changes something.
    applog.settings_changed(changes, stored, user=web._current_email())
    return jsonify({"ok": True, "settings": stored})


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
        conn = utils.get_db_connection()
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


# The games chassis, the ten rounds and the quiz live in `rounds.py` (#418).
# Imported for its **side effects** and nothing else: the routes, the activity
# context processor and `GAME_ROUNDS` all register themselves against the `app`
# object it takes from `web.py`, so there is nothing to bind here and no name to
# read back. Last, so that an import error names the module that failed rather
# than a half-built `app`.
import cards  # noqa: E402,F401
import chat  # noqa: E402,F401
import icons  # noqa: E402,F401
import rounds  # noqa: E402,F401


if __name__ == "__main__":
    # Local development is http://localhost, where a browser accepts no cookie
    # marked Secure — the gate pass would never stick (#274). Only this entry
    # point relaxes it, and the deployed WSGI process never runs it. An explicit
    # SESSION_COOKIE_SECURE in .env still wins, for anyone serving locally over
    # HTTPS or reproducing production behaviour.
    if "SESSION_COOKIE_SECURE" not in os.environ:
        app.config["SESSION_COOKIE_SECURE"] = False
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
