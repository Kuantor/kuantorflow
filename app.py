import os

from flask import Flask, flash, redirect, render_template, request, url_for

from parsers import parse_mht_file, parse_reverso_word
from utils import get_db_connection, get_flashcards_by_topic, save_flashcard

app = Flask(__name__)
# Needed for flash messages (session cookie). Override in production:
# set the SECRET_KEY environment variable on PythonAnywhere.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page: look up a Reverso word or upload an .mht notes file.
    Successful submissions save flashcards and redirect to the topic page.
    """
    message = None
    if request.method == "POST":
        action = request.form.get("action")
        topic = (request.form.get("topic") or "general").strip() or "general"
        try:
            if action == "parse_word":
                word = (request.form.get("word") or "").strip()
                if not word:
                    message = "Please enter a word."
                else:
                    entry = parse_reverso_word(word, topic=topic)
                    save_flashcard(entry)
                    flash((f"Saved to Database — '{word}' added to topic '{topic}'.", topic))
                    return redirect(url_for("index"))

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
                message = "Database connection successful!"
        except Exception as e:
            message = f"Error: {e}"

    return render_template("index.html", message=message)


@app.route("/flashcards/<topic>")
def flashcards(topic):
    """Display all flashcards saved under the given topic."""
    cards = get_flashcards_by_topic(topic)
    return render_template("flashcards.html", topic=topic, cards=cards)


if __name__ == "__main__":
    app.run(debug=True)
