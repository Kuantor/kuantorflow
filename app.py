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
except Exception as _agent_import_error:  # pragma: no cover - deployment-dependent
    MYKOLA_AVAILABLE = False
    # Say why, once, at import (#412). A missing agent is a supported state --
    # the two repos deploy in either order -- so this is not an error and does
    # not raise. But it used to be *silent*, and until #412 moved the key it
    # also meant `ANTHROPIC_API_KEY` never arrived, taking the word lookup's
    # translator and two activities with it. `applog` helpers never raise, so
    # this cannot turn a supported state into a failed import.
    applog.agent_unavailable(_agent_import_error)

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


def _save_and_log(entry, source, fills=None, allow_duplicate=False):
    """Save one card and record the outcome in logs/cards.log (#30).

    Every card written by the app goes through here or through the explicit
    applog calls next to the other utils.save_flashcard() call sites — keep it that
    way when adding a new save path.

    Returns True when a row was actually written (False = duplicate).

    Being the single funnel is also what makes #89 one change instead of four:
    every save path records its owner here.

    It is also where #125 is enforced, for the same reason: a save path that
    forgets to ask `can_add_cards()` first fails loudly here instead of
    quietly writing. Callers that face a person check beforehand, so the
    visitor gets the sign-in prompt rather than an error.
    """
    refusal = web.add_refusal()
    if refusal:
        applog.card_add_denied(entry, source=source, user=web._current_email(),
                               reason="blocked" if web.is_blocked() else "anonymous")
        raise PermissionError(refusal)
    # Which card this one is about to sit beside (#379), read *before* the
    # write while "the card with this word and pos" still names exactly one
    # row. Only for a deliberate duplicate, and only so the log line can say
    # what it duplicated -- a second row is otherwise indistinguishable later
    # from the accident #101 exists to prevent.
    alongside = None
    if allow_duplicate:
        try:
            existing = utils.find_duplicate(entry.get("word"), entry.get("pos"))
            alongside = existing[0] if existing else None
        except Exception:
            app.logger.exception("Could not read the card being duplicated")
    card_id = utils.save_flashcard(entry, added_by_user_id=web._current_user_id(),
                             allow_duplicate=allow_duplicate)
    if card_id is None:
        # A duplicate, but this lookup may still carry what the stored card is
        # missing -- which is the exit from #349's trap: a card saved during a
        # translator outage could otherwise never be improved, because looking
        # the word up again is exactly what #101 refuses.
        #
        # Still returns False. A fill is **not** a save, and saying otherwise
        # is how #308 had Mykola confirming a card that was never written.
        filled = utils.fill_missing_fields(entry)
        if filled:
            applog.card_filled(entry, filled, source=source,
                               user=web._current_email())
            if fills is not None:
                fills.append(filled)
        else:
            applog.card_skipped(entry, source=source, user=web._current_email())
        return False
    applog.card_created(entry, source=source, user=web._current_email(),
                        card_id=card_id, alongside=alongside)
    return True


# What a fill actually filled, in the words the popup uses for those fields
# (#377). A map rather than the labels on the card, because a language hidden
# in Settings travels as a hidden input with no label to borrow -- and a card
# whose Russian was filled should say so whether or not that field is on
# screen.
FILLED_FIELD_LABELS = {
    "explanation_en": "English explanation",
    "examples_en": "English examples",
    "translation_ukr": "Ukrainian translation",
    "examples_ukr": "Ukrainian examples",
    "translation_rus": "Russian translation",
    "examples_rus": "Russian examples",
}

# #186's sentence, said in two places since #377: after a save was skipped as a
# duplicate, and on the chip that says the card is a duplicate before anything
# is pressed. One string, because they are one fact told at two moments.
HIDDEN_DUPLICATE_NOTE = ("It is in the shared deck, hidden from you by your "
                         "'Use only individual cards' setting.")


def duplicate_notice(entries):
    """Extra sentence for a save skipped as a duplicate (#186), or None.

    Duplicate prevention (#101) is global, but #127 hides other people's cards
    — so "already in the database" can be said about a card the visitor cannot
    see, which reads as the app contradicting itself. Naming the setting turns
    a puzzle into a choice they can act on.

    Only ever an *addition* to the existing message: the plain wording is
    correct whenever the blocking card is one they can actually find.
    """
    if not web.current_settings()["individual_cards"]:
        return None
    owner = web._current_user_id()
    try:
        for entry in entries:
            existing = utils.find_duplicate(entry.get("word"), entry.get("pos"))
            if existing and existing[1] != owner:
                return HIDDEN_DUPLICATE_NOTE
    except Exception:
        # A dead database here costs a nicety, not the save path's answer.
        app.logger.exception("Could not check whether the duplicate is hidden")
    return None


def _word_already_saved(word):
    """Whether the word already has cards (issue #145). A DB error is treated
    as 'unknown' → no warning, so lookups keep working when the DB is down."""
    try:
        return utils.flashcard_word_exists(word)
    except Exception:
        return False


def _mark_already_saved(cards):
    """Tell each proposed card what the deck already holds for it (#377).

    The review popup used to say nothing until Add was pressed, and then said
    it on the button -- so with a dozen cards parsed from a file, finding out
    which ones were worth pressing cost a dozen presses.

    Two states, because #101 deduplicates on **word + part of speech** and a
    card parsed from notes often carries no part of speech at all:

    * `card` -- the same word and part of speech is already saved, so Add
      writes nothing. Not nothing at all: `utils.fill_missing_fields()` still fills
      what the stored card left empty (#349), and the sentence says so rather
      than implying a second copy;
    * `word` -- the word is saved under some other part of speech. This card
      *will* be added, as a second card, which is what #145 warns about one
      step earlier in the lookup panel.

    Telling them apart is the point. A chip reading "already in DB" on a card
    that is about to be added perfectly well is the same lie in the other
    direction.

    The sentence is built here rather than in the template or the script
    because it is shown in both -- as the chip's tooltip and as the question
    the confirmation asks -- and two copies of a sentence is two wordings by
    the end of the month.
    """
    try:
        states = utils.find_saved_words([(card.get("word"), card.get("pos"))
                                   for card in cards])
        # #186: duplicate detection is global while #127 hides other people's
        # cards, so "already in DB" can be said about a card the visitor
        # cannot find. Read once for the popup rather than per card.
        hidden_matters = web.current_settings()["individual_cards"]
        owner = web._current_user_id()
    except Exception:
        # Unknown, so nothing is claimed. The same answer #145's
        # `_word_already_saved()` gives to an unreachable database, for the
        # same reason: a popup that says less is better than one that does not
        # open. Everything the chips need is read in here for that reason --
        # the cards were parsed or looked up before this ran, and losing them
        # to a failed nicety would be the expensive half of the request thrown
        # away for the cheap one.
        app.logger.exception("Could not check which proposed cards are saved")
        return

    for card, state in zip(cards, states):
        card.update(_saved_mark(card.get("word"), card.get("pos"), state,
                                hidden_matters, owner))


def _saved_mark(word, pos, state, hidden_matters, owner):
    """The chip and the sentence for one card, or `{}` for a word nobody has.

    Its own function because #380 asks the same question again: the pencil
    changes the word in the browser, and the mark rendered here was an answer
    about the word the *parser* produced. `/saved.json` re-asks and hands back
    what this returns, so a renamed card cannot end up wearing a mark from a
    different word -- which would be #101 refusing in silence again, the
    failure #377 and #379 both exist to end.
    """
    pos = (pos or "").strip()
    if state["exact"]:
        detail = ("You already have a card for “%s”%s. Adding "
                  "this one keeps that card and saves this text beside it, "
                  "as a second card."
                  % (word, " (%s)" % pos if pos else ""))
        if hidden_matters and state["exact"][1] != owner:
            detail += " " + HIDDEN_DUPLICATE_NOTE
        return {"already": "card", "already_label": "Already in DB",
                "already_detail": detail}
    if state["others"]:
        saved_as = ", ".join(other or "no part of speech"
                             for other in state["others"])
        detail = ("You already have “%s” saved as %s. %s"
                  % (word, saved_as,
                     "This card is %s, so it will be added as a separate "
                     "card." % pos if pos else
                     "This card has no part of speech, so it will be added "
                     "as a separate card."))
        return {"already": "word", "already_label": "Word already saved",
                "already_detail": detail}
    return {}


# Every value `explanation_source` may hold (#390): the dictionaries the
# registry knows about, plus Reverso, which `lookup_word()` still falls back to
# for definitions without being a choice anybody can make.
EXPLANATION_SOURCES = frozenset(parsers.DICTIONARY_SLUGS) | {"reverso"}


def _text_source(field):
    """Which dictionary the dialog says wrote one of the fields it submits.

    `field` is `explanation_source` or `examples_source` -- two credits, because
    the two texts are edited separately (#390).

    Validated against the registry rather than trusted, because it arrives in a
    form field like everything else and a credit is a claim about somebody
    else's writing. An unrecognised value becomes **no credit**, which is the
    safe direction: a missing one shows nothing, where a wrong one puts a
    dictionary's name on a sentence it never wrote.

    Both dialogs clear the matching hidden field as soon as anybody types in
    the box beside it, so edited text arrives here with nothing to claim --
    which is the same answer `utils.update_flashcard()` reaches on its own.
    """
    value = (request.form.get(field) or "").strip().lower()
    return value if value in EXPLANATION_SOURCES else None


def _example_list(field):
    """One submitted examples field as a list of sentences, or None.

    Two shapes reach this and both are the app's own: the hidden JSON a card
    has carried since #134, and the one-per-line text of a textarea. The edit
    dialog has always sent lines (#176) and since #357 the review popup does
    too - but either dialog can still submit untouched JSON, so a reader that
    knows only one shape drops the other in silence, which is exactly how the
    popup's examples would be lost the moment they became editable.

    Not a general parser: anything that is neither shape counts as no
    examples, the same as an empty box.
    """
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return None
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


def _hidden_languages():
    """Language names this identity has hidden in Settings (#46/#79/#111),
    in the form the agent's whitelist expects — e.g. ["Russian"]."""
    prefs = web.current_settings()
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
    # ai_agent#50: deliberate less, answer shorter. Only ever passed when it is
    # on, so an agent that predates the parameter behaves as it always did.
    if "fast" in params and web.current_settings().get("mykola_fast_thinking"):
        kwargs["fast"] = True
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
        allowed, today = utils.claim_anonymous_message(ANONYMOUS_DAILY_LIMIT)
    except Exception:
        app.logger.exception("Could not count the anonymous message")
        allowed, today = True, 0
    if not allowed:
        applog.anonymous_limit_hit("daily", today, ANONYMOUS_DAILY_LIMIT)
        return jsonify({"error": BUSY_PROMPT, "sign_in_required": True}), 402

    session["anon_messages"] = used + 1
    return None


def _mykola_chat_inputs():
    """Every rule that decides whether a message may be answered at all.

    Returns `(payload, None)` with the question, history and chat id, or
    `(None, refusal)` — a Flask response to return unchanged.

    Split out because there are now two chat endpoints, the JSON one and the
    streamed one (ai_agent#50), and they must not acquire two sets of these
    rules. Every check here is somebody's ticket — the block (#126), the size
    cap, the anonymous quota (#164) — and a second copy is the one that goes
    stale.

    **The quota is claimed here, before any response begins.** A streamed
    response has its headers on the wire from the first byte, and a session
    write after that point never reaches the browser: the free-message counter
    would silently stop counting.
    """
    if not MYKOLA_AVAILABLE:
        return None, (jsonify({"error": "Mykola is not available on this server."}), 503)

    # A blocked account (#126) does not get the widget, but hiding it is
    # presentation: this is the refusal that holds for a request made by hand.
    # Before the length and content checks, so a blocked visitor cannot use
    # the endpoint's answers to probe anything.
    if web.is_blocked():
        applog.mykola_denied(user=web._current_email())
        return None, (jsonify({"error": web.blocked_notice()}), 403)

    if request.content_length and request.content_length > MAX_MYKOLA_REQUEST_BYTES:
        return None, (jsonify({"error": "Your message is too long. Please shorten it and try again."}), 413)

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    history = data.get("history", [])
    chat_id = _safe_chat_id(data.get("chat_id"))
    if not question:
        return None, (jsonify({"error": "Please type a question."}), 400)

    # Anonymous quota (#164) — checked before the model is called, so a
    # refused message costs nothing.
    refusal = _anonymous_quota_refusal()
    if refusal:
        return None, refusal

    return {"question": question, "history": history, "chat_id": chat_id}, None


def _handle_mykola_chat_request():
    """Shared JSON chat handler for widget and full ai_agent-style page."""
    payload, refusal = _mykola_chat_inputs()
    if refusal is not None:
        return refusal
    question, history, chat_id = (
        payload["question"], payload["history"], payload["chat_id"])

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
    user_id = web._current_user_id()
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
    prefix = _safe_email_prefix(web._current_email())
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
    Look up & save flow (issue: ai_agent#20).

    An anonymous visitor's card is refused (#125) by _save_and_log raising:
    the agent turns an exception from its card_saver into an error the model
    relays, so Mykola says he cannot save it and why, instead of claiming a
    card that was never written.

    **A skipped duplicate raises too** (#308). It used to return quietly, on
    the grounds that the card is in the database either way — which is true,
    and is not what the learner was told. They asked for it in *this* topic,
    duplicate detection is global while topics are not (#101), and the card
    can sit under a topic they never mentioned or, with #127 on, under
    somebody else's account where they cannot find it at all. Returning
    normally is what the agent reads as success: `_run_add_flashcard()`
    reports `saved` for any call that does not raise and ignores what the
    saver returns. So the false confirmation was manufactured here, not by
    the model — whose instructions already say never to claim a card was
    saved unless the tool returned success.

    Raising rather than returning a status keeps this to one repo and works
    against the ai_agent already deployed, which is the seam CLAUDE.md
    documents: a saver refuses by raising, and Mykola relays it in character.
    """
    if _save_and_log(entry, source="Mykola chat"):
        return entry

    # Stated as fact and nothing else. The message is relayed by the model, so
    # anything resembling an offer becomes a promise: an earlier draft ended
    # "you can move it if they would rather", and Mykola has no move tool —
    # which would have been this very bug wearing a different coat.
    word = entry.get("word") or "that word"
    named = f"'{word}' ({entry['pos']})" if entry.get("pos") else f"'{word}'"

    # #186's question, asked with #186's own code: is the blocking card even
    # visible to this learner? If not, its topic is not ours to name.
    hidden = duplicate_notice([entry])
    if hidden:
        raise RuntimeError(
            f"{named} is already saved, so no second copy was added. {hidden}")
    where = None
    try:
        where = utils.duplicate_topic(entry.get("word"), entry.get("pos"))
    except Exception:
        # A dead database costs the topic name, not the correction itself —
        # saying "already saved" without it still beats claiming it was.
        app.logger.exception("Could not find where the duplicate is filed")
    if where:
        raise RuntimeError(f"{named} is already saved under the topic "
                           f"'{where}', so no second copy was added.")
    raise RuntimeError(f"{named} is already saved, so no second copy was added.")


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
    user_id = web._current_user_id()
    if user_id is None:
        raise PermissionError(
            "Sign in with Google and I shall remember what to call you.")
    if not utils.set_preferred_name(user_id, name):
        raise RuntimeError("I could not find your account to note that in.")

    user = dict(session.get("user") or {})
    user["preferred_name"] = name
    session["user"] = user
    applog.preferred_name_set(user_id, name, user=web._current_email())
    return name


# What Mykola reads to know the app he lives in (#310). The learner's guide,
# and not a second description written for him: it is maintained here beside
# the app it describes, reviewed like any other change, and shipped to learners
# as a PDF — so it is already kept true, and a copy in ai_agent would be one
# more thing to remember to update. There used to be such a copy, and it
# drifted until it was answering "why can't I add cards?" from a description
# written before sign-in was required to write at all.
#
# A list, because this is the seam through which anything else the app knows
# about itself would arrive. Paths that do not exist are skipped by the agent
# in silence.
# Where a card goes when nobody chose a topic (#414).
#
# **One declaration**, for the reason `TRANSLATORS` and `ACTIVITIES` are each
# declared once: this used to be the string "general" typed out at every use,
# and #407 renamed that topic in both databases without the code noticing. Every
# lookup saved with the box left empty then recreated the topic #407 had just
# removed -- the consolidation quietly undoing itself, two cards at a time.
#
# It is also injected into Mykola (see `get_mykola()`), so a card saved from a
# conversation lands in the same place as one saved from the page.
DEFAULT_TOPIC = "General knowledge"


MYKOLA_KNOWLEDGE = [Path(__file__).parent / "docs" / "user-guide.md"]


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
        if "knowledge_docs" in accepted:                  # #310
            kwargs["knowledge_docs"] = MYKOLA_KNOWLEDGE
        if "default_topic" in accepted:                   # #414
            # Where a card with no topic mentioned in conversation goes. The
            # agent keeps its own "general" for standalone runs against its own
            # database; this is the deck's answer, and the deck is ours.
            kwargs["default_topic"] = DEFAULT_TOPIC
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
    owner = web.cards_owner_filter()
    return [{"topic": name, "cards": count}
            for _section, topics in web._sections_for_visitor(owner)
            for name, count in topics]


# The translation column each hideable language lives in (#46/#79).
_TRANSLATION_COLUMNS = {"Ukrainian": "translation_ukr",
                        "Russian": "translation_rus"}


def _cards_for_chat(topic, limit):
    """One topic's cards, filtered exactly as the browse page filters them.

    Through `utils.get_flashcards_by_topic()` with `cards_owner_filter()`, so #127
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
    cards = utils.get_flashcards_by_topic(topic, web.cards_owner_filter(), **web.viewer())[:limit]
    if not hidden:
        return cards
    return [{k: v for k, v in card.items() if k not in hidden}
            for card in cards]


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


@app.context_processor
def inject_mykola():
    """Expose whether the chat widget should render.

    A blocked account (#126) does not get the widget. That is presentation —
    the endpoints refuse the request themselves — but leaving a chat box that
    answers every message with a refusal would be worse than not offering it.
    """
    return {
        "mykola_enabled": MYKOLA_AVAILABLE and not web.is_blocked(),
        "app_boot_id": APP_BOOT_ID,
        "mykola_identity": web._identity_token(),
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

    result["cards"] = utils.resolve_user_cards(user_id, keep_cards)

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


def _sse(payload) -> str:
    """One Server-Sent Event carrying a JSON object.

    `ensure_ascii` stays on: the frame travels as one line, and a stray raw
    newline inside a Ukrainian reply would end the event early and split one
    message into two malformed ones.
    """
    return "data: " + json.dumps(payload, ensure_ascii=True) + "\n\n"


@app.route("/mykola/chat/stream", methods=["POST"])
def mykola_chat_stream():
    """The same answer as `/mykola/chat`, sent as it is written (ai_agent#50).

    The agent has streamed from the API since it was written; until now the
    fragments were joined into a string before anything outside the call could
    see one, so the learner watched a spinner while the text sat in a Python
    variable. This carries `stream_answer()`'s deltas to the browser.

    **404 when the installed ai_agent cannot stream**, which is how the widget
    knows to use the JSON endpoint instead. The two repos deploy in either
    order (the same reason `get_mykola()` feature-detects its savers), so a
    kuantorflow that has been pulled and an ai_agent that has not must degrade
    to yesterday's behaviour rather than to an error.

    Everything the JSON endpoint decides is decided *before* the first byte,
    in `_mykola_chat_inputs()`: a refusal is still an ordinary JSON response
    with an ordinary status code. Once the stream opens the status is 200 for
    good, so a failure after that point can only be an `error` event.
    """
    agent = get_mykola() if MYKOLA_AVAILABLE else None
    if agent is None or not hasattr(agent, "stream_answer"):
        abort(404)

    payload, refusal = _mykola_chat_inputs()
    if refusal is not None:
        return refusal
    question, history, chat_id = (
        payload["question"], payload["history"], payload["chat_id"])
    kwargs = _agent_kwargs(agent.stream_answer)

    def events():
        try:
            for kind, data in agent.stream_answer(question, history, **kwargs):
                if kind == "text":
                    yield _sse({"type": "delta", "text": data})
                    continue
                # The closing event: the words are already on screen, but the
                # sources, the history and — the one that matters — which
                # cards were saved are only known now.
                _append_chat_log(chat_id, question, data.get("response", ""))
                yield _sse(dict(data, type="done", chat_id=chat_id))
        except Exception as e:
            # Guarded, unlike the JSON path's plain import: this runs inside
            # the generator, so an ImportError here would replace the error
            # message with a broken stream — the one moment the learner most
            # needs a sentence they can read. Mykola is optional, and a server
            # without the anthropic package is a server where that import is
            # exactly the thing that fails.
            try:
                import anthropic
                api_error = isinstance(e, anthropic.APIError)
            except Exception:
                api_error = False
            if api_error:
                body, _status = api_error_response(e)
            else:
                app.logger.exception("Mykola chat stream failed")
                body = {"error": "Internal server error. Please try again later."}
            yield _sse(dict(body, type="error"))

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # A buffering proxy would hold every fragment and deliver the reply
            # in one piece at the end — the exact behaviour this endpoint
            # exists to remove, and invisible from the code. nginx honours this
            # header; anything else in front of the app has to be checked.
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/mykola/recap", methods=["POST"])
def mykola_recap():
    """Welcome-back recap of the signed-in user's previous conversations
    (issue ai_agent#30). The recap is an optional nicety: anonymous visitors,
    empty histories, older agent versions, and errors all return
    {"recap": null} so the widget silently keeps its normal greeting."""
    if not MYKOLA_AVAILABLE or not session.get("user") or web.is_blocked():
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
    if web.is_blocked():
        # No conversation to restart — the widget is not there (#126).
        return jsonify({"restart": False, "reason": "blocked"})
    hours = web.current_settings()["restart_chat_interval"]
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
        conn = utils.get_db_connection()
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
    owner = web.cards_owner_filter()
    try:
        topics = utils.get_topics(owner, **web.viewer())
        sections = web._sections_for_visitor(owner)
    except Exception:
        topics, sections = [], []
    # Icons ride alongside as a name -> URL map rather than as a third element
    # in each pair (#223). The pair is what `utils.get_topics_by_section()` returns and
    # what the move dialog reads; widening it would push a presentation concern
    # into the database layer and into every existing reader.
    icons = {name: topic_icon(name)
             for _, pairs in sections for name, _ in pairs
             if topic_icon(name)}
    # The padlocks ride along like the icons (#382/#223): `refreshBrowseTopics()`
    # rebuilds the very block index.html renders, so a mark drawn on one and not
    # the other would vanish the moment a card was saved from chat.
    return jsonify({"topics": topics, "sections": sections, "icons": icons,
                    "private": web._private_marks()})


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
    proposed_degraded = False   # the cards carry no translations (#349)
    source_content = None  # readable text of an upload, shown beside its cards
    duplicate_warning = None  # the word to warn about before looking it up (#145)
    write_refusal = None  # why a write was refused, if one was (#125/#126)
    sign_in_refusal = None  # a lookup refused where signing in helps (#388)
    if request.method == "POST":
        action = request.form.get("action")
        topic = ((request.form.get("topic") or DEFAULT_TOPIC).strip()
                 or DEFAULT_TOPIC)
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
                    # #388. Asked here: after #145's duplicate warning, so
                    # a word the learner has not decided about costs
                    # nothing, and before any provider, so a refusal
                    # spends nothing at all. Asked **once** and kept --
                    # `_lookup_refusal()` claims the slot as it answers,
                    # so a second call would take a second slot.
                    lookup_refusal = web._lookup_refusal()
                    if lookup_refusal:
                        # A ceiling an account has already reached is not
                        # something signing in fixes; the anonymous ones
                        # are, and they say so through the dialog #125's
                        # write refusal already uses.
                        if lookup_refusal["sign_in"]:
                            sign_in_refusal = lookup_refusal["message"]
                        else:
                            message = lookup_refusal["message"]
                    else:
                        prefs = web.current_settings()
                        # The provider-by-provider detail is logged by the parser;
                        # this line carries the identity it cannot see (#30).
                        applog.lookup_started(
                            word, prefs["translator"],
                            prefs["explanatory_dictionary"], user=web._current_email())
                        entries = lookup_word(
                            word, topic=topic,
                            translator=prefs["translator"],
                            explanatory_dictionary=prefs["explanatory_dictionary"],
                        )
                        # #349: the dictionary answered and no translator did, so
                        # these cards carry an explanation and no translations.
                        #
                        # **Where this is said depends on where the learner is
                        # looking.** A page-level banner is unreadable behind the
                        # review popup -- `.modal-overlay` is fixed, inset 0, 75%
                        # opaque and blurred -- and that popup is the default, so
                        # the first version of this notice was invisible to most
                        # people while passing a test that only checked the HTML.
                        # The banner is kept for the automatic save, which has no
                        # popup at all; the popup carries its own, next to the
                        # empty fields and phrased around what to do about them.
                        degraded = bool(entries) and not any(
                            entry.get("translation_ukr")
                            or entry.get("translation_rus")
                            for entry in entries)
                        if prefs["cards_automatically"] and not web.can_add_cards():
                            # #125/#126: nothing may be written, so the automatic
                            # save cannot happen. The lookup already succeeded, so
                            # show its cards in the review popup rather than
                            # throwing the work away — signing in from the prompt
                            # leaves them there to be added.
                            applog.card_add_denied(
                                {"word": word}, source="automatic add",
                                user=web._current_email(),
                                reason="blocked" if web.is_blocked() else "anonymous")
                            proposed = entries
                            proposed_topic = topic
                            proposed_degraded = degraded
                            write_refusal = web.add_refusal()
                        elif prefs["cards_automatically"]:
                            # 'Add cards automatically' is on (#13): skip the
                            # review popup, write the cards straight to the DB.
                            # Duplicates are skipped and reported (issue #101).
                            if degraded:
                                # Said as *the service is unavailable* rather than
                                # *this word has no translations*, which is the
                                # wrong sentence that sent #348's investigation at
                                # the wrong provider.
                                flash(("No translation service is answering just "
                                       f"now, so '{word}' was saved with its "
                                       "English explanation and examples only. "
                                       "Look it up again later and the "
                                       "translations will be filled in.", None))
                            fills = []
                            added = sum(
                                1 for entry in entries
                                if _save_and_log(entry, source="automatic add",
                                                 fills=fills)
                            )
                            skipped = len(entries) - added
                            # A duplicate that gained something is not "nothing
                            # added" (#349), and a learner repeating a lookup to
                            # repair a card needs to hear that it worked.
                            completed = (f" Completed {len(fills)} of them with "
                                         "this lookup." if fills else "")
                            if not added:
                                note = duplicate_notice(entries)   # #186
                                flash((f"All {skipped} card(s) for '{word}' are "
                                       "already in the database — nothing added."
                                       + completed
                                       + (f" {note}" if note else ""), None))
                            elif skipped:
                                flash((f"Added {added} card(s) for '{word}' "
                                       f"automatically, skipped {skipped} already "
                                       "in the database." + completed, topic))
                            else:
                                flash((f"Added {added} card(s) for '{word}' automatically.", topic))
                            return redirect(url_for("index"))
                        # Don't save yet: show the cards for review/editing first.
                        proposed = entries
                        proposed_topic = topic
                        proposed_degraded = degraded

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
                write_refusal = web.add_refusal()
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
                                             user=web._current_email())
                        raise
                    applog.file_parsed(file.filename, len(data), len(entries),
                                       topic=topic, user=web._current_email(),
                                       elapsed_ms=timer.ms)
                    if not entries:
                        message = "No vocabulary entries found in that file."
                    else:
                        proposed = entries
                        proposed_topic = topic

        except Exception as e:
            message = f"Error: {e}"

    if proposed:
        # #377. Here rather than in either branch above: both the word lookup
        # and the notes upload end at the same popup, and what the deck already
        # holds is a question about the cards, not about where they came from.
        _mark_already_saved(proposed)

    try:
        sections = web._sections_for_visitor()
    except Exception:
        sections = []  # DB unreachable (e.g. locally) — page still works

    return render_template(
        "index.html", message=message, sections=sections,
        private_marks=web._private_marks(),
        proposed=proposed, proposed_topic=proposed_topic,
        proposed_degraded=proposed_degraded,
        source_content=source_content, duplicate_warning=duplicate_warning,
        write_refusal=write_refusal, sign_in_refusal=sign_in_refusal,
    )


@app.route("/cards/add", methods=["POST"])
def add_card():
    """
    Save one reviewed (possibly edited) card from the lookup popup.
    Returns JSON so the popup can stay open for the remaining cards.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    word = cleaned("word")
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    entry = {
        "word": word,
        "pos": cleaned("pos"),
        "topic": cleaned("topic") or DEFAULT_TOPIC,
        "explanation_en": cleaned("explanation_en"),
        "explanation_source": _text_source("explanation_source"),
        "examples_en": _example_list("examples_en"),
        "examples_source": _text_source("examples_source"),
        "translation_ukr": cleaned("translation_ukr"),
        "examples_ukr": _example_list("examples_ukr"),
        "translation_rus": cleaned("translation_rus"),
        "examples_rus": _example_list("examples_rus"),
    }
    refusal = web.add_refusal()
    if refusal:
        # #125/#126. Answered here rather than by hiding the button: these
        # forms are ordinary POSTs and a hand-made one goes straight past the
        # UI. `sign_in_required` is what tells the popup to show the message
        # instead of its generic "saving failed" alert.
        applog.card_add_denied(entry, source="review popup",
                               user=web._current_email(),
                               reason="blocked" if web.is_blocked() else "anonymous")
        return {"ok": False, "sign_in_required": True, "error": refusal}, 403
    # The learner was shown the card they already have and answered "add it
    # anyway" (#379). #101 is lifted for this one press: it exists to stop
    # repeated lookups piling up rows nobody asked for, and somebody who has
    # read the confirmation is not making that mistake. Everything else --
    # every other save path, and this one without the flag -- still refuses.
    #
    # Read from the form because the popup is the only thing that can have
    # asked. A hand-made POST can set it too, and the cost is a second card in
    # the sender's own deck.
    anyway = (request.form.get("confirmed_duplicate") or "").strip() in (
        "1", "true", "yes")
    # What the press *did* when it did not write a card (#377). A skipped
    # duplicate still fills whatever the stored card left empty (#349), and
    # this route was the one surface that never said so: the automatic-add
    # path has reported "Completed N of them with this lookup" since #349,
    # while the popup's button said "Already in DB" over a card it had just
    # changed. Which reads as "nothing happened" -- the wrong half of the
    # truth, and the half that matters least to somebody who pressed Add.
    fills = []
    if not _save_and_log(entry, source="review popup", fills=fills,
                         allow_duplicate=anyway):
        # #101 skipped it; #186 explains when the blocking card is hidden.
        # The keys are present only when there is something extra to say, so
        # the ordinary duplicate answer keeps its existing shape.
        body = {"ok": True, "saved": False, "duplicate": True}
        if fills:
            body["filled"] = [FILLED_FIELD_LABELS.get(field, field)
                              for field in fills[0]]
        note = duplicate_notice([entry])
        if note:
            body["note"] = note
        return body
    return {"ok": True, "saved": True}


TOPIC_VISIBILITY_MESSAGES = {
    "changed": None,          # the page redraws; the select says it already
    "unchanged": None,
    "denied": "That topic is not yours to hide.",
    "nobodys": "A topic with no creator cannot be made private — there is "
               "nobody for it to belong to.",
    "shared": "This topic holds cards other people added, so it cannot be made "
              "private. Move those cards to another topic first, and everyone "
              "keeps what they saved.",
    "taken": "You already have a topic with that name in the other "
             "visibility. Rename one of them first.",
    "missing": "That topic no longer exists.",
}


@app.route("/topics/<int:topic_id>/visibility", methods=["POST"])
def topic_visibility(topic_id):
    """Make one topic public or private (#382).

    A write, so it is a POST and it is logged (#30) -- including its refusals,
    which are the two answers a learner will report as "it did nothing".

    The permission is `utils.set_topic_visibility()`'s, not this route's: the rule is
    "your own topic", and it belongs beside the UPDATE for the reason #162 and
    #176 put ownership in the statement rather than in a check before it. The
    template only decides what to draw.
    """
    if web.is_blocked():
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=request.form.get("topic", "")))
    public = (request.form.get("visibility") or "public") == "public"
    outcome = utils.set_topic_visibility(topic_id, public, **web.viewer())
    name = request.form.get("topic", "")
    applog.topic_visibility_set(name, public, topic_id=topic_id,
                                user=web._current_email(), outcome=outcome)
    message = TOPIC_VISIBILITY_MESSAGES.get(outcome)
    if message:
        flash((message, None))
    return redirect(url_for("flashcards", topic=name))


@app.route("/flashcards/<topic>")
def flashcards(topic):
    """Display all flashcards saved under the given topic.

    Since #382 the topic is **resolved** before it is read, and a name that
    resolves to nothing this visitor may see is a 404. Leaving it out of every
    list is not enough: a name reaches this route from a URL somebody kept, a
    bookmark, or a guess, and the page would otherwise open empty and tell them
    the topic is there but has no cards.

    `?t=` settles which topic when a name matches two -- the public one and a
    private one -- which only the admin can ever see. It is checked against the
    same rule rather than trusted.
    """
    wanted = request.args.get("t", type=int)
    found = utils.resolve_topic(topic, topic_id=wanted, **web.viewer())
    if found is None:
        abort(404)
    cards = utils.get_flashcards_by_topic(found["name"], web.cards_owner_filter(),
                                    **web.viewer())
    # The move dialog's topic suggestions (#177) are fetched from
    # /topics.json when it first opens, rather than queried here: this page is
    # loaded by everyone and the list is only needed by someone who actually
    # moves a card.
    return render_template(
        "flashcards.html", topic=found["name"], cards=cards, topic_row=found,
        # Who may change it, which is not the same as who may see it: the
        # admin reads every topic (#382) and still does not own this one.
        can_set_visibility=(found["created_by_user_id"] is not None
                            and found["created_by_user_id"] == web._current_user_id()))


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
    prefs = web.current_settings()
    try:
        cards = utils.get_flashcards_by_topic(topic, web.cards_owner_filter(), **web.viewer())
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
    if web.is_blocked():
        # #126: a blocked account keeps its cards but may not remove them,
        # exactly as it may not add any. Checked before ownership, so the
        # answer does not depend on whose card it is.
        applog.card_delete_denied(card_id, topic=topic, user=web._current_email(),
                                  reason="blocked")
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))

    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        # No identity at all — an anonymous visitor, or a sign-in whose users
        # row could not be written (#148). Nothing can be theirs, so this is
        # #125's sign-in prompt rather than #162's "someone else's card".
        applog.card_delete_denied(card_id, topic=topic, user=web._current_email(),
                                  reason="anonymous")
        flash((web.DELETE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))

    word, outcome = utils.delete_flashcard(card_id, owner_id=user_id, admin=admin)
    if outcome == "deleted":
        applog.card_deleted(card_id, word, topic=topic, user=web._current_email())
        flash((f"Deleted card '{word}'.", None))
    elif outcome == "denied":
        applog.card_delete_denied(card_id, topic=topic,
                                  user=web._current_email(), reason="not owner")
        flash((web.DELETE_NOT_YOURS, None))
    else:
        applog.card_delete_missed(card_id, topic=topic, user=web._current_email())
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

    if web.is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="blocked")
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))
    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="anonymous")
        flash((web.MOVE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))
    if not to_topic:
        flash(("Choose a topic to move the card to.", None))
        return redirect(url_for("flashcards", topic=topic))

    outcome, detail = utils.move_flashcard(card_id, to_topic, owner_id=user_id,
                                     admin=admin)
    if outcome == "denied":
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="not owner")
        flash((web.MOVE_NOT_YOURS, None))
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
                      user=web._current_email())
    flash((f"Moved '{word}' to '{to_topic}'.", to_topic))

    # Moving the last card out of a topic makes that topic cease to exist —
    # there is no topics table. Landing back on a page that no longer has
    # anything to show, for a topic that has vanished from the chips, reads as
    # a bug; the topic list is the honest destination.
    try:
        remaining = [name for name, _ in utils.get_topics(web.cards_owner_filter(), **web.viewer())]
    except Exception:
        remaining = [from_topic]      # DB unreachable: stay put rather than guess
    if from_topic not in remaining:
        return redirect(url_for("index"))
    return redirect(url_for("flashcards", topic=from_topic))


# How many words one session may have checked against a lexicon in an hour
# (#258). Not a money guard -- both lookups are free -- but Wikimedia
# rate-limits what looks like a scraper, and being throttled would turn every
# dispute into "could not check" for everybody. A round offers at most five
# disputes, so this is roughly eight rounds' worth back to back.
WORD_CHECKS_PER_HOUR = 40


def _word_check_allowed():
    """Whether this session may spend another lexicon lookup (#258).

    Counted in the session rather than the database: it protects our own
    politeness rather than anything a learner owns, and a counter that resets
    when a cookie does is the right weight for that. A confirmed word is
    answered from the table without a lookup at all, so the cap is only ever
    reached by a genuine run of new disputes -- or by somebody driving the
    endpoint, which is what it is for.
    """
    now = time.time()
    started, count = session.get("word_checks", (0, 0))
    if now - started > 3600:
        started, count = now, 0
    if count >= WORD_CHECKS_PER_HOUR:
        return False
    session["word_checks"] = (started, count + 1)
    return True


@app.route("/games/word-check.json", methods=["POST"])
def word_check():
    """Settle a word *Real or fake* called invented (#258).

    The learner disputes; a real lexicon answers; the answer is kept so the
    word is never offered as invented again. Three outcomes and only one of
    them is a verdict -- `real: false` means both lexicons answered and
    neither had it, which is **not** proof the word was invented, and
    `real: null` means nothing could be reached at all.

    A word already in the table is answered from it, with no request to
    anybody: a settled question stays settled, and the second learner to
    dispute it pays nothing.
    """
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    if web.is_blocked():
        return {"ok": False, "error": web.blocked_notice()}, 403

    if word.lower() in utils.confirmed_words():
        return {"ok": True, "real": True, "known": True,
                "source": "a check somebody already made"}
    if not _word_check_allowed():
        return {"ok": True, "real": None,
                "error": "That is a lot of words to check at once. "
                         "Try again a little later."}

    verdict = parsers.confirm_word(word)
    if verdict.get("real"):
        try:
            first = utils.remember_confirmed_word(word, verdict.get("source", ""))
            applog.word_confirmed(word, verdict.get("source", ""),
                                  user=web._current_email(), first=first)
        except Exception:
            # The confirmation still stands for this learner and this round;
            # it simply was not remembered for the next one.
            app.logger.exception("Could not remember a confirmed word")
    return dict({"ok": True}, **verdict)


@app.route("/saved.json", methods=["POST"])
def saved_json():
    """Whether one word is already in the deck, as JSON (#380).

    #377 marks every proposed card when the popup is built, on the server, for
    the word the *parser* produced. The pencil changes that word afterwards in
    the browser, so the card asks this and re-marks itself: the chip appears,
    changes or goes, and the confirmation #379 made worth answering asks about
    the word that will actually be saved. Without it a rename onto a word the
    deck already holds would be refused in silence by #101 -- the failure both
    of those tickets exist to end.

    A **read**, and cheaper than the page that asks it: one query, no provider
    call, nothing written. So it asks for no permission the review popup does
    not already have -- unlike `/lookup.json`, which spends money and wants an
    account for it (#125).

    An unreachable database answers `known: false` rather than an error, and
    the card drops its mark instead of wearing one from a different word: the
    same "say nothing rather than something wrong" `_mark_already_saved()`
    answers with, and #145 before it.
    """
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    pos = (data.get("pos") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    try:
        state = utils.find_saved_words([(word, pos or None)])[0]
        hidden_matters = web.current_settings()["individual_cards"]
        owner = web._current_user_id()
    except Exception:
        app.logger.exception("Could not check whether a renamed word is saved")
        return {"ok": True, "known": False, "mark": {}}
    return {"ok": True, "known": True,
            "mark": _saved_mark(word, pos, state, hidden_matters, owner)}


@app.route("/lookup.json", methods=["POST"])
def lookup_json():
    """One word, looked up with this visitor's providers, as JSON (#191).

    A **read**. Nothing is stored: the edit dialog fills its own fields from
    the answer and `edit_card()` remains the only way a card changes, keeping
    its ownership rule, its duplicate check and its logging as the single
    write path. That is the whole design constraint of #191 -- a second write
    path would be a second place to get permissions wrong.

    Guarded like the dialog it serves rather than like the home page's lookup.
    A lookup used to be free scraping and #125 lets an anonymous visitor read;
    since #353 it is a licensed API call that costs money per word, so this is
    not something to leave open, and the edit dialog behind it is signed-in
    only anyway (#176).

    **The part of speech is matched here**, not in the browser. #228's synonym
    map lives in `parsers` because a translator and a dictionary name the same
    thing differently, and a second copy of it in JavaScript would drift from
    the first within a month. The answer carries both halves: `match` for the
    part of speech that was asked about, and `entries` for everything the
    lookup found, so the dialog can offer a choice when nothing matched.
    """
    if web.is_blocked():
        return {"ok": False, "error": web.blocked_notice()}, 403
    if not web.is_admin() and web._current_user_id() is None:
        return {"ok": False, "error": web.EDIT_SIGN_IN_PROMPT}, 403

    payload = request.get_json(silent=True) or {}
    word = (payload.get("word") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    pos = (payload.get("pos") or "").strip()

    # #388's account ceiling applies here too, and leaving it out would be a
    # hole in it rather than a smaller cap: this spends the same providers on
    # the same key, and a learner past their day's lookups could carry on
    # through the edit dialog. Anonymous visitors never reach it -- refused
    # above by #191 -- so only the per-account row is ever claimed.
    refusal = web._lookup_refusal()
    if refusal:
        return {"ok": False, "error": refusal["message"]}, 429

    prefs = web.current_settings()
    applog.lookup_started(word, prefs["translator"],
                          prefs["explanatory_dictionary"],
                          user=web._current_email())
    try:
        entries = lookup_word(
            word,
            translator=prefs["translator"],
            explanatory_dictionary=prefs["explanatory_dictionary"],
        )
    except Exception:
        # The same tolerance the home page has: a provider outage is not a
        # 500, and the dialog says so with its fields untouched.
        app.logger.exception("Look up & update failed for %r", word)
        return {"ok": False, "error": (
            "The lookup did not answer. Your card is unchanged.")}, 502

    entries = [{k: v for k, v in entry.items() if k != "topic"}
               for entry in entries]
    wanted = parsers._pos_key(pos.lower()) if pos else None
    match = next((e for e in entries
                  if wanted and parsers._pos_key((e.get("pos") or "").lower())
                  == wanted), None)
    return {"ok": True, "match": match, "entries": entries}


@app.route("/flashcards/<topic>/edit/<int:card_id>", methods=["POST"])
def edit_card(topic, card_id):
    """Change a saved card's content (#176). JSON, so the dialog can stay open
    and show a refusal in place rather than losing what was typed.

    Enforced here rather than in the template for the same reason as #162:
    greying the pencil is presentation, and a hand-made POST goes past it.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    if web.is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="blocked")
        return {"ok": False, "error": web.blocked_notice()}, 403
    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="anonymous")
        return {"ok": False, "error": web.EDIT_SIGN_IN_PROMPT}, 403

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
        "examples_en": _example_list,
        "examples_ukr": _example_list,
        "examples_rus": _example_list,
    }
    entry = {field: (read() if field == "word" else read(field))
             for field, read in readers.items() if field in request.form}
    # A credit travels with the text it belongs to or not at all (#390).
    # #191's dialog fills these boxes from a fresh lookup, so either really can
    # hold a dictionary's words -- and it clears the matching hidden field the
    # moment somebody edits a box, which leaves this None and
    # `utils.update_flashcard()` clearing the stored credit for that field alone.
    for text, credit in (("explanation_en", "explanation_source"),
                         ("examples_en", "examples_source")):
        if text in entry:
            entry[credit] = _text_source(credit)

    outcome, detail = utils.update_flashcard(card_id, entry, owner_id=user_id,
                                       admin=admin)
    if outcome == "updated":
        applog.card_edited(entry, source="card page", user=web._current_email(),
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
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="not owner")
        return {"ok": False, "error": web.EDIT_NOT_YOURS}, 403
    return {"ok": False, "error": "Card not found — it may have been deleted."}, 404


# --- building a topic from an idea (#406) ---------------------------------
#
# Four routes and a session key, in the shape #237 already uses: the expensive
# half is post/redirect/get so a refresh cannot pay for it twice, and the part
# that takes a minute streams rather than sitting on a request.
#
#   GET  /topics/generate          the form
#   POST /topics/generate          propose a title and a word list -- nothing
#                                  written, no lookup spent
#   POST /topics/generate/start    claim the lookups, hold the approved list
#   GET  /topics/generate/filling  the progress page, which opens...
#   GET  /topics/generate/stream   ...the SSE fill that does the work
#
# The approved list lives in the **session**, as #237's held text does: a title
# and twenty short words is a few hundred bytes of the signed cookie's ~4 KB,
# and holding it is what lets the fill be a GET that EventSource can open.
TOPIC_PLAN_KEY = "topic_plan"

# A pause between words, `seed_topics.PAUSE`'s value and its reason: this fans
# out at no dictionary and is in no hurry. Twenty words is a minute of somebody
# else's bandwidth, which is why the page streams rather than waiting.
TOPIC_FILL_PAUSE = 1.0


def _lookup_budget(wanted):
    """What this fill would cost, in the learner's own daily terms.

    Stated on the approve screen rather than discovered afterwards, which is
    the whole of #406's decision about the ceiling: a mid-run refusal becomes a
    number somebody read before pressing the button.
    """
    user_id = session.get("user", {}).get("id")
    limit = web.LOOKUP_USER_DAILY if user_id is not None else web.LOOKUP_ANON_DAILY
    if not limit or limit <= 0:
        return {"limit": 0, "used": 0, "left": None, "wanted": wanted,
                "affordable": True}
    try:
        used = utils.lookups_used_today(user_id)
    except Exception:
        # An unreachable counter cannot answer and the claim itself will
        # decide. Better a screen with no number than one with a wrong number.
        app.logger.exception("Could not read today's lookup count")
        return {"limit": limit, "used": None, "left": None, "wanted": wanted,
                "affordable": True}
    left = max(limit - used, 0)
    return {"limit": limit, "used": used, "left": left, "wanted": wanted,
            "affordable": wanted <= left}


def _vet_proposal(title, words, idea, count):
    """The proposal with the free checks already done (#391's idea, #389's tool).

    Two things are settled before the learner spends anything, because both are
    free:

    * **a word no lexicon has** is usually the model inflecting or inventing,
      and #221 is what it costs to find out afterwards -- a word with no
      dictionary entry becomes a card carrying translations and no explanation,
      which is invisible locally because Reverso covers the gap.
      `parsers.wiktionary_pages()` answers the whole list in one request (#389),
      and an unreachable lexicon leaves them simply unflagged;
    * **a word the deck already holds** is worth saying out loud, because a
      lookup that produces nothing still costs a slot.

    Only the first is **unticked**. A word already in the deck is a *note*, not
    a veto: #101 keeps one card per word and part of speech, so a deck holding
    `tip` the noun still gains `tip` the verb, and unticking it would be the
    screen claiming something it does not know. Nothing is removed from the
    list either way -- the one thing this must not do is quietly shorten a list
    somebody asked for.
    """
    try:
        have = utils.existing_words()
    except Exception:
        app.logger.exception("Could not read the deck's words")
        have = set()
    found = parsers.wiktionary_pages(words)

    entries = []
    for word in words:
        missing = found is not None and word.lower() not in found
        entries.append({
            "word": word,
            "already": word.lower() in have,
            "unknown": missing,
            "use": not missing,
        })
    wanted = sum(1 for entry in entries if entry["use"])
    return {
        "title": title or idea[:topicgen.TITLE_MAX_CHARS],
        "entries": entries,
        "idea": idea,
        "count": count,
        "checked": found is not None,
        "budget": _lookup_budget(wanted),
    }


@app.route("/topics/generate", methods=["GET", "POST"])
def generate_topic():
    """Propose a topic from an idea. Writes nothing and spends no lookup.

    **The write guard runs before the model call.** That is #200's rule and the
    reason `upload_notes` asks `add_refusal()` at the door before it reads the
    file: an anonymous visitor cannot save a card (#125), so proposing twenty
    of them would be paying for a refusal.
    """
    if not web._generation_available():
        abort(404)

    refusal = web.add_refusal()
    proposal, message = None, None

    if request.method == "POST" and not refusal:
        idea = topicgen.clean_idea(request.form.get("idea"))
        count = topicgen.word_count(request.form.get("count"))
        if not idea:
            message = "Please describe the topic you have in mind."
        else:
            spend = web._generation_refusal()
            if spend:
                message = spend["message"]
            else:
                title, words = topicgen.propose(idea, count)
                if not words:
                    message = ("That topic could not be proposed just now. "
                               "Please try again in a moment.")
                else:
                    proposal = _vet_proposal(title, words, idea, count)

    return render_template(
        "generate_topic.html", proposal=proposal, message=message,
        refusal=refusal, idea=request.form.get("idea", ""),
        count=topicgen.word_count(request.form.get("count")),
        min_words=topicgen.MIN_WORDS, max_words=topicgen.MAX_WORDS,
        idea_max=topicgen.IDEA_MAX_CHARS,
        title_max=topicgen.TITLE_MAX_CHARS)


@app.route("/topics/generate/start", methods=["POST"])
def start_topic_fill():
    """Claim the lookups for an approved list and hold it for the fill.

    **The claim is here, before the redirect, and it is all-or-nothing.** That
    is #406's decision about the ceiling: the approve screen states the cost,
    this takes it in one statement, and a refusal arrives while the learner is
    still looking at the list rather than halfway through a half-built topic.

    Post/redirect/get afterwards, #237's shape, so a refresh of the progress
    page re-reads the held plan instead of claiming a second batch.
    """
    if not web._generation_available():
        abort(404)
    refusal = web.add_refusal()
    if refusal:
        flash((refusal, None))
        return redirect(url_for("generate_topic"))

    title = " ".join((request.form.get("title") or "").split())
    title = title[:topicgen.TITLE_MAX_CHARS]
    # The words that were **ticked**, in the order the form sent them. A word
    # the learner unticked is not looked up and not paid for.
    words, seen = [], set()
    for word in request.form.getlist("word"):
        word = (word or "").strip().lower()
        if word and word not in seen and topicgen.HEADWORD.match(word):
            seen.add(word)
            words.append(word)

    if not title or not words:
        flash(("Choose a title and at least one word first.", None))
        return redirect(url_for("generate_topic"))

    user_id = session.get("user", {}).get("id")
    try:
        allowed, scope, used = utils.claim_word_lookups(
            user_id, len(words), web.LOOKUP_USER_DAILY, web.LOOKUP_ANON_DAILY)
    except Exception:
        # #237's rule for an unreachable counter: it cannot enforce a ceiling,
        # and the same outage has already made the deck unwritable.
        app.logger.exception("Could not claim the lookups for a topic")
        allowed, scope = True, None

    if not allowed:
        limit = web.LOOKUP_USER_DAILY if scope == "user" else web.LOOKUP_ANON_DAILY
        applog.anonymous_limit_hit(scope, used, limit)
        flash((f"That would need {len(words)} lookups and you have "
               f"{max(limit - used, 0)} left today. Untick a few words, or "
               "come back tomorrow.", None))
        return redirect(url_for("generate_topic"))

    session[TOPIC_PLAN_KEY] = {"title": title, "words": words}
    return redirect(url_for("filling_topic"))


@app.route("/topics/generate/filling")
def filling_topic():
    """The progress page. Opens the stream that does the work."""
    if not web._generation_available():
        abort(404)
    plan = session.get(TOPIC_PLAN_KEY)
    if not plan:
        return redirect(url_for("generate_topic"))
    return render_template("filling_topic.html", plan=plan)


@app.route("/topics/generate/stream")
def stream_topic_fill():
    """Look each approved word up and save it, reporting as it goes.

    **Streamed rather than waited on.** `seed_topics.py`'s docstring says why
    twenty words is not a request: it pauses between lookups and fans out at no
    dictionary, so this is a minute of work. `/mykola/chat/stream` proved SSE
    reaches the browser through PythonAnywhere's proxy unbuffered, and this
    reuses its headers for the same reason.

    **The lookups were already claimed** by `start_topic_fill()`, before this
    response opened -- a session write after the first byte never reaches the
    browser, which is the trap that shape avoids.

    **Saved as it goes, so it is resumable.** Each card is committed on arrival,
    so a dropped connection leaves the words that worked, and #101's duplicate
    rule makes "ask for it again" the whole of the recovery. A failed lookup is
    a skipped word rather than a dead run: `lookup_word()` raises when nothing
    comes back, and Reverso and Merriam-Webster are blocked from PythonAnywhere.
    """
    if not web._generation_available():
        abort(404)
    plan = session.get(TOPIC_PLAN_KEY) or {}
    words = list(plan.get("words") or [])
    title = plan.get("title") or ""
    prefs = web.current_settings()
    user = web._current_email()
    # Read before the response opens: `session` is not writable from inside a
    # generator, and `add_refusal()` reads the request context.
    refusal = web.add_refusal()

    def events():
        if refusal or not words or not title:
            yield _sse({"type": "error", "message": refusal or
                        "There is nothing to build."})
            return
        saved = skipped = failed = 0
        for index, word in enumerate(words, start=1):
            try:
                entries = lookup_word(
                    word, prefs.get("translator"),
                    prefs.get("explanatory_dictionary"))
            except Exception as error:
                failed += 1
                applog.lookup_failed(word, error)
                yield _sse({"type": "word", "word": word, "index": index,
                            "total": len(words), "outcome": "failed"})
                time.sleep(TOPIC_FILL_PAUSE)
                continue

            added = 0
            for entry in entries:
                entry["topic"] = title
                if _save_and_log(entry, source="topic generator"):
                    added += 1
            saved += added
            if not added:
                skipped += 1
            yield _sse({"type": "word", "word": word, "index": index,
                        "total": len(words), "cards": added,
                        "outcome": "saved" if added else "skipped"})
            time.sleep(TOPIC_FILL_PAUSE)

        applog.topic_generated(title, len(words), saved, skipped=skipped,
                               failed=failed, user=user)
        yield _sse({"type": "done", "title": title, "saved": saved,
                    "skipped": skipped, "failed": failed,
                    "url": url_for("flashcards", topic=title)})

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# The games chassis, the ten rounds and the quiz live in `rounds.py` (#418).
# Imported for its **side effects** and nothing else: the routes, the activity
# context processor and `GAME_ROUNDS` all register themselves against the `app`
# object it takes from `web.py`, so there is nothing to bind here and no name to
# read back. Last, so that an import error names the module that failed rather
# than a half-built `app`.
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
