import os
import re
import sys
import uuid
from pathlib import Path
from datetime import datetime

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
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.middleware.proxy_fix import ProxyFix

from parsers import parse_google_word, parse_mht_file
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

# PythonAnywhere serves the app behind a proxy; trust its X-Forwarded-*
# headers so absolute URLs (og:image etc.) use https and the real host.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Keyword that gates access to the whole site (set ACCESS_KEYWORD in .env).
ACCESS_KEYWORD = os.environ.get("ACCESS_KEYWORD", "password")

# Reject oversized Tynna chat payloads before they reach the model — guards
# against memory blowups and runaway Anthropic API costs. Scoped to the chat
# endpoint rather than a global MAX_CONTENT_LENGTH, so it can't clip legitimate
# (and much larger) .mht uploads on the index route. 1 MB is generous for text.
MAX_TYNNA_REQUEST_BYTES = 1024 * 1024

LOG_DIR = Path(__file__).parent / "tynna_logs"
LOG_DIR.mkdir(exist_ok=True)

# --- Tynna AI chat, imported from the ai_agent repo (NOT duplicated here) ---
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
    # base module. If the repo or its deps are missing, Tynna is simply disabled.
    from agent import TynnaAgent, api_error_response
    TYNNA_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the deployment environment
    TYNNA_AVAILABLE = False

_tynna_agent = None


def _safe_chat_id(raw_chat_id: str | None) -> str:
    """Allow only safe filename chars and always return a usable chat id."""
    if not raw_chat_id:
        return f"web_{uuid.uuid4().hex}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw_chat_id.strip())
    return safe[:64] or f"web_{uuid.uuid4().hex}"


def _append_chat_log(chat_id: str, user_text: str, assistant_text: str) -> None:
    """Append one user/assistant exchange to tynna_logs/chat_<chat_id>.txt."""
    log_path = LOG_DIR / f"chat_{chat_id}.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}]\n")
        f.write("User:\n")
        f.write((user_text or "").strip() + "\n\n")
        f.write("Tynna:\n")
        f.write((assistant_text or "").strip() + "\n")
        f.write("\n" + ("=" * 60) + "\n\n")

# When the agent repo is present, let kuantorflow's Jinja also find its
# templates (e.g. the shared _tynna_about.html partial). kuantorflow's own
# templates stay first, so they win on any name clash (base.html, etc.).
if TYNNA_AVAILABLE:
    app.jinja_loader = ChoiceLoader([
        app.jinja_loader,
        FileSystemLoader(os.path.join(AI_AGENT_PATH, "templates")),
    ])


@app.context_processor
def inject_tynna_media():
    """Resolve Tynna asset names to the /tynna-media route (which serves them
    straight from the ai_agent repo). Mirrors the helper ai_agent defines for
    itself, so the shared About partial's asset URLs work here too."""
    return {"tynna_media": lambda f: url_for("tynna_media_file", filename=f)}


@app.route("/tynna-media/<path:filename>")
def tynna_media_file(filename):
    """Serve Tynna's images/video from the ai_agent repo — no copies in this
    repo. Behind the keyword gate like everything else."""
    if not TYNNA_AVAILABLE:
        abort(404)
    return send_from_directory(os.path.join(AI_AGENT_PATH, "static", "img"), filename)


@app.route("/tynna/about")
def tynna_about():
    """About-Tynna page: kuantorflow chrome wrapping the shared ai_agent partial."""
    if not TYNNA_AVAILABLE:
        abort(404)
    return render_template("tynna_about.html")


def get_tynna():
    """Lazily build the TynnaAgent (loads the knowledge base) on first use."""
    global _tynna_agent
    if _tynna_agent is None:
        _tynna_agent = TynnaAgent()
    return _tynna_agent


@app.context_processor
def inject_tynna():
    """Expose whether the chat widget should render."""
    return {"tynna_enabled": TYNNA_AVAILABLE}


@app.before_request
def require_keyword():
    """Block every page behind the keyword gate until it's been entered."""
    if session.get("access_granted"):
        return None
    # The gate page and static assets (its image, CSS, favicon) must load.
    if request.endpoint in ("gate", "static"):
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


@app.route("/tynna/chat", methods=["POST"])
def tynna_chat():
    """
    Chat endpoint for the Tynna widget. Delegates to the imported TynnaAgent
    (from the ai_agent repo) and returns its {response, sources, history} JSON.
    Behind the keyword gate like every other route.
    """
    if not TYNNA_AVAILABLE:
        return jsonify({"error": "Tynna is not available on this server."}), 503

    if request.content_length and request.content_length > MAX_TYNNA_REQUEST_BYTES:
        return jsonify({"error": "Your message is too long. Please shorten it and try again."}), 413

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    history = data.get("history", [])
    chat_id = _safe_chat_id(data.get("chat_id"))
    if not question:
        return jsonify({"error": "Please type a question."}), 400

    try:
        result = get_tynna().answer(question, history)
        _append_chat_log(chat_id, question, result.get("response", ""))
        result["chat_id"] = chat_id
        return jsonify(result)
    except Exception as e:  # format Anthropic errors nicely, log the rest
        import anthropic
        if isinstance(e, anthropic.APIError):
            body, status = api_error_response(e)
            return jsonify(body), status
        app.logger.exception("Tynna chat failed")
        return jsonify({"error": "Internal server error. Please try again later."}), 500


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page: look up a Reverso word or upload an .mht notes file.
    Successful submissions save flashcards and redirect to the topic page.
    """
    message = None
    proposed = None
    proposed_topic = None
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
                    entries = parse_mht_file(file.read(), topic=topic)
                    if not entries:
                        message = "No 'word — explanation' lines found in that file."
                    else:
                        for entry in entries:
                            save_flashcard(entry)
                        flash((
                            f"Saved to Database — {len(entries)} flashcard(s) "
                            f"from '{file.filename}' added to topic '{topic}'.",
                            topic,
                        ))
                        return redirect(url_for("index"))

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
