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
    if cards._save_and_log(entry, source="Mykola chat"):
        return entry

    # Stated as fact and nothing else. The message is relayed by the model, so
    # anything resembling an offer becomes a promise: an earlier draft ended
    # "you can move it if they would rather", and Mykola has no move tool —
    # which would have been this very bug wearing a different coat.
    word = entry.get("word") or "that word"
    named = f"'{word}' ({entry['pos']})" if entry.get("pos") else f"'{word}'"

    # #186's question, asked with #186's own code: is the blocking card even
    # visible to this learner? If not, its topic is not ours to name.
    hidden = cards.duplicate_notice([entry])
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
            kwargs["default_topic"] = cards.DEFAULT_TOPIC
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
    rows = utils.get_flashcards_by_topic(topic, web.cards_owner_filter(), **web.viewer())[:limit]
    if not hidden:
        return rows
    return [{k: v for k, v in card.items() if k not in hidden}
            for card in rows]


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
                    yield web._sse({"type": "delta", "text": data})
                    continue
                # The closing event: the words are already on screen, but the
                # sources, the history and — the one that matters — which
                # cards were saved are only known now.
                _append_chat_log(chat_id, question, data.get("response", ""))
                yield web._sse(dict(data, type="done", chat_id=chat_id))
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
            yield web._sse(dict(body, type="error"))

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


# The games chassis, the ten rounds and the quiz live in `rounds.py` (#418).
# Imported for its **side effects** and nothing else: the routes, the activity
# context processor and `GAME_ROUNDS` all register themselves against the `app`
# object it takes from `web.py`, so there is nothing to bind here and no name to
# read back. Last, so that an import error names the module that failed rather
# than a half-built `app`.
import cards  # noqa: E402,F401
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
