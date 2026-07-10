import os

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
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
