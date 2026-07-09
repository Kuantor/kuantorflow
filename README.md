# kuantorflow
Interactive language learning with notes and dictionaries

KuantorFlow is a flexible language‑learning platform built with Flask.  
It transforms notes, dictionary lookups, and study materials into interactive activities such as explanations, translations, flashcards, and quizzes.

---

## 🚀 Features
- Store words with **English explanations** and optional **Ukrainian/Russian translations**.
- Add **examples in multiple languages** to show real usage.
- Upload notes (e.g. `.mht` files from OneNote) and convert them into structured learning entries.
- Organize flashcards by topic for easy browsing.
- Practice with quizzes generated from your saved entries.

---

## 📂 Project Structure

kuantorflow/
├── app.py              # Main Flask application (routes, views)
├── utils.py            # Database connection + helper functions
├── requirements.txt    # Python dependencies
├── README.md           # Project documentation
├── .gitignore          # Ignore secrets, venv, cache files
├── templates/          # HTML templates (index.html, flashcards.html, quiz.html)
├── static/             # CSS, JS, images
└── uploads/            # Uploaded MHT files (optional)
