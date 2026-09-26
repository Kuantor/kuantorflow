"""Mykola: the chat widget, the agent it wraps, and the per-user chat logs (#418).

The last block out of `app.py`, and the one the ticket called biggest. It
moved **after** `cards.py` rather than before it, because Mykola's card saver
is a card save -- `_save_card_from_chat()` writes through
`cards._save_and_log()` and `get_mykola()` hands him `cards.DEFAULT_TOPIC`,
so this module has to be able to import that one.

**The agent is imported, never duplicated.** `AI_AGENT_PATH` goes on
`sys.path` here and `MykolaAgent` comes off it; a missing repo is a supported
state, not an error, so `MYKOLA_AVAILABLE` goes False and the widget does not
render. That import lives here rather than in `app.py` because everything that
uses it does.

`_mykola_agent` is a **module-level singleton** built on first use by
`get_mykola()`. It is one per process on purpose -- building the agent loads
its knowledge base -- which is why this cache has to live in exactly one
module, and why that module is this one rather than wherever the next caller
appears.

`MYKOLA_KNOWLEDGE` resolves `docs/user-guide.md` relative to **this file**, so
this module has to stay beside `app.py` in the repo root. An absent path is
skipped by the agent *in silence*, which is #310's failure exactly: Mykola
carries on answering questions about the app from nothing. That is recorded on
#442, which is where any move into a package has to deal with it.
"""

import inspect
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from jinja2 import ChoiceLoader, Environment, FileSystemLoader

import applog
import cards
import utils
import web
from web import (
    app,
    ANONYMOUS_DAILY_LIMIT,
    ANONYMOUS_MESSAGE_LIMIT,
    APP_BOOT_ID,
    LOG_DIR,
    MAX_MYKOLA_REQUEST_BYTES,
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
    if web.ANONYMOUS_MESSAGE_LIMIT and used >= web.ANONYMOUS_MESSAGE_LIMIT:
        applog.anonymous_limit_hit("session", used, web.ANONYMOUS_MESSAGE_LIMIT,
                                   log=applog.MYKOLA, action="chat")
        return jsonify({"error": SIGN_IN_PROMPT, "sign_in_required": True}), 402

    # The anonymous *daily* row moved into `web.chat_refusal()` with #456,
    # which claims it beside everybody's in one call -- claiming here too
    # would spend two slots for one message. What is left here is the session
    # nudge, which is a cookie rather than a row and therefore free to check.
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

    if request.content_length and request.content_length > web.MAX_MYKOLA_REQUEST_BYTES:
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

    # And the account's own day (#447). Signing in used to remove the ceiling
    # rather than raise it, which was right behind the keyword gate and is not
    # once anybody can reach the site: every message is an Anthropic call, and
    # one purchased Google account is not a barrier to anything.
    #
    # Here rather than in the routes because all three of them — the widget's
    # POST, ai_agent's /api/chat and the SSE stream — come through this
    # function, so a message is claimed once however it was asked.
    spent = web.chat_refusal()
    if spent:
        return None, (jsonify({"error": spent["message"],
                               "sign_in_required": spent["sign_in"]}), 402)

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
        return web.LOG_DIR
    user_dir = web.LOG_DIR / str(user_id)
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
    legacy = web.LOG_DIR / prefix
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
    if user_dir == web.LOG_DIR:
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
    if user_dir == web.LOG_DIR:          # the same test _user_log_files() makes
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


# The one declaration lives in `web.py` since #460, because the Help page
# renders the same file -- see `web.USER_GUIDE` for why that matters.
MYKOLA_KNOWLEDGE = [web.USER_GUIDE]


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
def inject_mykola():
    """Expose whether the chat widget should render.

    A blocked account (#126) does not get the widget. That is presentation —
    the endpoints refuse the request themselves — but leaving a chat box that
    answers every message with a refusal would be worse than not offering it.
    """
    return {
        "mykola_enabled": MYKOLA_AVAILABLE and not web.is_blocked(),
        "app_boot_id": web.APP_BOOT_ID,
        "mykola_identity": web._identity_token(),
    }


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

    # The account's day (#447), and **after** the farewell check above, which
    # is deterministic and costs nothing: claiming before it would spend a slot
    # on a message the model never writes. Every guard here sits after the free
    # refusals and immediately before the call it pays for.
    #
    # A refusal is `{"recap": None}`, which is what every other thing that can
    # go wrong here answers — the recap is an optional nicety, so the widget
    # keeps its normal greeting rather than showing an error.
    if web.account_refusal(utils.RECAP, utils.RECAP_ALL,
                           web.RECAP_USER_DAILY, web.RECAP_ALL_DAILY,
                           web.RECAP_USER_LIMIT_PROMPT):
        return jsonify({"recap": None})

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
