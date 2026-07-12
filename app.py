import inspect
import os
import re
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Flask,
    abort,
    flash,
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

from parsers import parse_google_word, parse_mht_preview
from utils import (
    delete_flashcard,
    get_db_connection,
    get_flashcards_by_topic,
    get_topics,
    save_flashcard,
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

# --- Optional "Sign in with Google" (OAuth 2.0 / OpenID Connect) -------------
# Purely optional: a visitor can sign in to be greeted by name, or stay
# anonymous ("invisible") and use the site exactly as before. Enabled only when
# both credentials are set (see .env.example) — otherwise it disables itself and
# no sign-in button is shown. The identity lives only in the Flask session; no
# IP address or name is ever written to the database.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTH_AVAILABLE = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

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
    """First name of the signed-in visitor, if any — for Mykola to address them
    by (the opening greeting uses the full name; the chat uses the first name)."""
    name = ((session.get("user") or {}).get("name") or "").strip()
    return name.split()[0] if name else None


def _agent_answer(question, history):
    """Call the agent, passing the signed-in first name when the installed
    ai_agent version supports it. Feature-detected so the chat keeps working
    even if the ai_agent side hasn't been updated yet."""
    agent = get_mykola()
    first_name = _current_first_name()
    if first_name and "user_name" in inspect.signature(agent.answer).parameters:
        return agent.answer(question, history, user_name=first_name)
    return agent.answer(question, history)


def _handle_mykola_chat_request():
    """Shared JSON chat handler for widget and full ai_agent-style page."""
    if not MYKOLA_AVAILABLE:
        return jsonify({"error": "Mykola is not available on this server."}), 503

    if request.content_length and request.content_length > MAX_MYKOLA_REQUEST_BYTES:
        return jsonify({"error": "Your message is too long. Please shorten it and try again."}), 413

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    history = data.get("history", [])
    chat_id = _safe_chat_id(data.get("chat_id"))
    if not question:
        return jsonify({"error": "Please type a question."}), 400

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


def _safe_chat_id(raw_chat_id: str | None) -> str:
    """Allow only safe filename chars and always return a usable chat id."""
    if not raw_chat_id:
        return f"web_{uuid.uuid4().hex}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw_chat_id.strip())
    return safe[:64] or f"web_{uuid.uuid4().hex}"


def _safe_email_prefix(email: str | None) -> str | None:
    """Filesystem-safe directory name from the part of an email before the @."""
    prefix = (email or "").split("@", 1)[0].strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]", "_", prefix).strip("._")
    return safe[:64] or None


def _current_user_log_dir() -> Path:
    """Log directory for this request: mykola_logs/<email_prefix>/ for
    signed-in visitors (issue ai_agent#30), the shared mykola_logs/ otherwise."""
    email = (session.get("user") or {}).get("email")
    prefix = _safe_email_prefix(email)
    if not prefix:
        return LOG_DIR
    user_dir = LOG_DIR / prefix
    user_dir.mkdir(exist_ok=True)
    return user_dir


def _read_user_logs(max_chars: int = 12000) -> str:
    """Most recent chat-log text of the signed-in user, chronological order,
    capped at max_chars. Empty string for anonymous users or no history."""
    user_dir = _current_user_log_dir()
    if user_dir == LOG_DIR:
        return ""
    files = sorted(user_dir.glob("chat_*.txt"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    collected, total = [], 0
    for path in files:  # newest first, stop once the budget is spent
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        collected.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(reversed(collected))[-max_chars:]


def _append_chat_log(chat_id: str, user_text: str, assistant_text: str) -> None:
    """Append one user/assistant exchange to chat_<chat_id>.txt in the shared
    log dir, or in the signed-in user's subdirectory."""
    log_path = _current_user_log_dir() / f"chat_{chat_id}.txt"
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
    Look up & save flow (issue: ai_agent#20)."""
    save_flashcard(entry)
    return entry


def get_mykola():
    """Lazily build the MykolaAgent (loads the knowledge base) on first use."""
    global _mykola_agent
    if _mykola_agent is None:
        # Inject our DB writer when the installed agent supports it —
        # feature-detected so older ai_agent checkouts keep working.
        kwargs = {}
        if "card_saver" in inspect.signature(MykolaAgent.__init__).parameters:
            kwargs["card_saver"] = _save_card_from_chat
        _mykola_agent = MykolaAgent(**kwargs)
    return _mykola_agent


@app.context_processor
def inject_mykola():
    """Expose whether the chat widget should render."""
    return {
        "mykola_enabled": MYKOLA_AVAILABLE,
        "app_boot_id": APP_BOOT_ID,
    }


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
    # Persist the signed-in session across browser restarts (30 days, see
    # app.permanent_session_lifetime). Anonymous sessions stay non-permanent.
    session.permanent = True
    session["user"] = {
        "name": info.get("name") or info.get("given_name") or "there",
        "email": info.get("email"),
        "picture": info.get("picture"),
    }
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    """Sign out: drop the session identity and return to anonymous browsing."""
    session.pop("user", None)
    return redirect(url_for("index"))


@app.context_processor
def inject_auth():
    """Expose the signed-in user (if any) and whether Google sign-in is on."""
    return {
        "current_user": session.get("user"),
        "google_auth_enabled": GOOGLE_AUTH_AVAILABLE,
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


@app.route("/mykola/recap", methods=["POST"])
def mykola_recap():
    """Welcome-back recap of the signed-in user's previous conversations
    (issue ai_agent#30). The recap is an optional nicety: anonymous visitors,
    empty histories, older agent versions, and errors all return
    {"recap": null} so the widget silently keeps its normal greeting."""
    if not MYKOLA_AVAILABLE or not session.get("user"):
        return jsonify({"recap": None})
    agent = get_mykola()
    if not hasattr(agent, "recap"):  # older ai_agent checkout
        return jsonify({"recap": None})
    logs = _read_user_logs()
    if not logs:
        return jsonify({"recap": None})
    try:
        text = agent.recap(logs, user_name=_current_first_name())
        return jsonify({"recap": text or None})
    except Exception:
        app.logger.exception("Mykola recap failed")
        return jsonify({"recap": None})


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page: look up a Reverso word or upload an .mht notes file.
    Successful submissions save flashcards and redirect to the topic page.
    """
    message = None
    proposed = None
    proposed_topic = None
    mht_content = None  # readable text of an uploaded .mht, shown beside its cards
    if request.method == "POST":
        action = request.form.get("action")
        topic = (request.form.get("topic") or "general").strip() or "general"
        try:
            if action == "parse_word":
                word = (request.form.get("word") or "").strip()
                if not word:
                    message = "Please enter a word."
                else:
                    # Don't save yet: show the cards for review/editing first.
                    proposed = parse_google_word(word, topic=topic)
                    proposed_topic = topic

            elif action == "upload_mht":
                file = request.files.get("mht_file")
                if file is None or not file.filename:
                    message = "Please choose an .mht file."
                else:
                    # Don't save yet: show the parsed cards next to the file
                    # content for review/editing, like the word lookup does.
                    entries, mht_content = parse_mht_preview(file.read(), topic=topic)
                    if not entries:
                        message = "No 'word — explanation' lines found in that file."
                    else:
                        proposed = entries
                        proposed_topic = topic

            elif action == "test_db":
                conn = get_db_connection()
                conn.close()
                flash(("Database connection successful!", None))
                return redirect(url_for("index"))
        except Exception as e:
            message = f"Error: {e}"

    try:
        topics = get_topics()
    except Exception:
        topics = []  # DB unreachable (e.g. locally) — page still works

    return render_template(
        "index.html", message=message, topics=topics,
        proposed=proposed, proposed_topic=proposed_topic,
        mht_content=mht_content,
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
        "topic": cleaned("topic") or "general",
        "explanation_en": cleaned("explanation_en"),
        "translation_ukr": cleaned("translation_ukr"),
        "translation_rus": cleaned("translation_rus"),
    }
    save_flashcard(entry)
    return {"ok": True}


@app.route("/flashcards/<topic>")
def flashcards(topic):
    """Display all flashcards saved under the given topic."""
    cards = get_flashcards_by_topic(topic)
    return render_template("flashcards.html", topic=topic, cards=cards)


@app.route("/flashcards/<topic>/delete/<int:card_id>", methods=["POST"])
def delete_card(topic, card_id):
    """Delete one flashcard and return to its topic page."""
    word = delete_flashcard(card_id)
    if word:
        flash((f"Deleted card '{word}'.", None))
    else:
        flash(("Card not found — it may have already been deleted.", None))
    return redirect(url_for("flashcards", topic=topic))


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


@app.route("/quiz/<topic>", methods=["GET", "POST"])
def quiz(topic):
    """
    Quiz on the given topic: show each English word, the user types the
    translation in the chosen language (?lang=rus or ?lang=ukr, Russian
    by default). Cards without that translation are skipped.
    On POST, grade the answers and show the results.
    """
    lang = request.args.get("lang", "rus")
    if lang not in QUIZ_LANGS:
        lang = "rus"
    field = f"translation_{lang}"

    cards = [c for c in get_flashcards_by_topic(topic) if c.get(field)]

    if request.method == "POST":
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
        return render_template(
            "quiz.html", topic=topic, cards=cards,
            lang=lang, lang_name=QUIZ_LANGS[lang], langs=QUIZ_LANGS,
            results=results, score=score,
        )

    return render_template(
        "quiz.html", topic=topic, cards=cards,
        lang=lang, lang_name=QUIZ_LANGS[lang], langs=QUIZ_LANGS,
        results=None,
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
