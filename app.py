from flask import Flask, render_template, request
from utils import get_db_connection

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    """
    Basic landing page.
    For now, just confirms DB connection works.
    Later steps (Claude Code) will add parsing, saving, and flashcard routes.
    """
    message = None
    if request.method == "POST":
        try:
            conn = get_db_connection()
            conn.close()
            message = "Database connection successful!"
        except Exception as e:
            message = f"Database connection failed: {e}"

    return render_template("index.html", message=message)


if __name__ == "__main__":
    app.run(debug=True)
