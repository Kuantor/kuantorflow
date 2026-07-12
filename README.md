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
- Chat with **Tynna**, an AI study assistant, from a widget in the corner.

---

## 🤖 Tynna AI chat

A small chat widget sits in the bottom‑right corner of every page. It talks to
**Tynna**, the RAG (retrieval‑augmented) study assistant that lives in the
separate [`ai_agent`](https://github.com/Kuantor/ai_agent) repository.

**How the integration works — the agent code is imported, never duplicated:**

1. `app.py` adds the `ai_agent` checkout to `sys.path` (path from the
   `AI_AGENT_PATH` env var; defaults to a sibling folder `../ai_agent`) and
   imports `TynnaAgent` from it. If the repo or its dependencies aren't present,
   `TYNNA_AVAILABLE` is `False` and the widget simply doesn't render.
2. The `/tynna/chat` route (behind the keyword gate, like everything else)
   receives `{question, history}`, calls `TynnaAgent.answer(...)`, and returns
   `{response, sources, history}` as JSON. All the RAG + Claude logic runs
   inside `ai_agent`; KuantorFlow only wires up the route and the UI.
3. The widget (in `templates/base.html`) posts to that route and renders
   Tynna's replies, including which knowledge‑base documents were used.

Because PythonAnywhere allows only one web app, Tynna cannot run as its own
Flask app in production — so its chat endpoint is served *by KuantorFlow*, but
the intelligence still comes straight from the `ai_agent` package.

**Deployment (PythonAnywhere):**

- Clone `ai_agent` next to `kuantorflow` (`/home/<user>/ai_agent`), so the
  default `AI_AGENT_PATH` finds it (or set `AI_AGENT_PATH` explicitly).
- Install its dependencies into KuantorFlow's virtualenv
  (`anthropic`, `scikit-learn`; already added to `requirements.txt`).
- Put `ANTHROPIC_API_KEY` in `ai_agent/.env` — Tynna reads its own key there,
  separate from KuantorFlow's `.env`.
- Reload the web app. With no key or credits, Tynna returns a friendly
  "out of tokens / set your key" message instead of a raw error.

---

## 👋 Welcome screen popup

KuantorFlow now shows a two-step welcome popup when the page loads:

1. Step 1 shows `static/img/main_image.png` and a `Next >` button in the
   bottom-right area.
2. Step 2 introduces Tynna with title/text and image (`tynna.jpg` via
   `/tynna-media` when available; falls back to local avatar otherwise).
3. Pressing `Start Learning!` closes the popup and reveals the main app page.

Implementation details:

- Markup and behavior are in `templates/base.html`.
- Styling is in `static/css/style.css` under the "Welcome popup" section.
- No extra Flask route is required; it is template-driven and compatible with
  the existing layout and gate flow.

---

## 📂 Project Structure

```
kuantorflow/
├── app.py              # Main Flask application (routes, views)
├── utils.py            # Database connection + helper functions
├── requirements.txt    # Python dependencies
├── README.md           # Project documentation
├── .gitignore          # Ignore secrets, venv, cache files
├── templates/          # HTML templates (index.html, flashcards.html, quiz.html)
├── static/             # CSS, JS, images
└── uploads/            # Uploaded MHT files (optional)
```
