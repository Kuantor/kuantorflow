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
- Chat with **Mykola**, an AI study assistant, from a widget in the corner.

---

## 🤖 Mykola AI chat

A small chat widget sits in the bottom‑right corner of every page. It talks to
**Mykola**, the RAG (retrieval‑augmented) study assistant that lives in the
separate [`ai_agent`](https://github.com/Kuantor/ai_agent) repository.

**How the integration works — the agent code is imported, never duplicated:**

1. `app.py` adds the `ai_agent` checkout to `sys.path` (path from the
   `AI_AGENT_PATH` env var; defaults to a sibling folder `../ai_agent`) and
   imports `MykolaAgent` from it. If the repo or its dependencies aren't present,
   `MYKOLA_AVAILABLE` is `False` and the widget simply doesn't render.
2. The `/mykola/chat` route (behind the keyword gate, like everything else)
   receives `{question, history}`, calls `MykolaAgent.answer(...)`, and returns
   `{response, sources, history}` as JSON. All the RAG + Claude logic runs
   inside `ai_agent`; KuantorFlow only wires up the route and the UI.
3. The widget (in `templates/base.html`) posts to that route and renders
   Mykola's replies, including which knowledge‑base documents were used.

Because PythonAnywhere allows only one web app, Mykola cannot run as its own
Flask app in production — so its chat endpoint is served *by KuantorFlow*, but
the intelligence still comes straight from the `ai_agent` package.

**Deployment (PythonAnywhere):**

- Clone `ai_agent` next to `kuantorflow` (`/home/<user>/ai_agent`), so the
  default `AI_AGENT_PATH` finds it (or set `AI_AGENT_PATH` explicitly).
- Install its dependencies into KuantorFlow's virtualenv
  (`anthropic`, `scikit-learn`; already added to `requirements.txt`).
- Put `ANTHROPIC_API_KEY` in `ai_agent/.env` — Mykola reads its own key there,
  separate from KuantorFlow's `.env`.
- Reload the web app. With no key or credits, Mykola returns a friendly
  "out of tokens / set your key" message instead of a raw error.

---

## 👋 Welcome screen popup

KuantorFlow now shows a two-step welcome popup when the page loads:

1. Step 1 shows `static/img/main_image.jpg` and a `Next >` button in the
   bottom-right area.
2. Step 2 introduces Mykola with title/text and image (`mykola_poster.jpg` via
   `/mykola-media` when available; falls back to local avatar otherwise).
3. Pressing `Start Learning!` closes the popup and reveals the main app page.

Implementation details:

- Markup and behavior are in `templates/base.html`.
- Styling is in `static/css/style.css` under the "Welcome popup" section.
- No extra Flask route is required; it is template-driven and compatible with
  the existing layout and gate flow.

---

## ⚙️ Settings

User preferences live in JSON files under `settings/`, one per identity
(issue #86):

```
settings/config-default.json      every anonymous (not signed-in) visitor
settings/config-<username>.json   one per Google-authorised user
```

`<username>` is the part of the user's email before the `@`, sanitised to be
filesystem-safe — so signed-in users never share preferences, and anonymous
visitors share the default config.

`settings_store.py` is the whole storage layer, and `settings_store.DEFAULTS`
is the source of truth for which settings exist:

| Setting | Default | Introduced for |
| --- | --- | --- |
| `cards_automatically` | `false` | #13 — save looked-up cards without the review popup |
| `translator` | `google` | #20 — `google` or `bing` |
| `explanatory_dictionary` | `oxford` | #20 — `oxford` or `merriam-webster` |
| `show_ukrainian` | `true` | #46 — hide Ukrainian everywhere |
| `show_russian` | `true` | #46 — hide Russian everywhere |

Behaviour worth knowing:

- **The files are optional.** A missing, unreadable or corrupt config falls
  back to the defaults rather than breaking the page, so nothing needs to be
  provisioned on deploy — the directory alone is enough.
- **Adding a setting** means adding one entry to `DEFAULTS`; existing config
  files pick up the new default on their next read, with no migration.
- **Values are validated** on read and write: unknown keys are dropped and
  out-of-range values fall back to their default.
- **Writes are atomic**, so a crash mid-write can't corrupt a config.
- The files are runtime state and are **gitignored**; `SETTINGS_DIR` can
  override the location.

In Python, `app.current_settings()` returns the active settings for the
request, and every template gets them as `settings`:

```python
if current_settings()["cards_automatically"]:
    ...
```

The Settings UI itself arrives in #13, #20 and #46 — this is the store they
read from.

---

## 📂 Project Structure

```
kuantorflow/
├── app.py              # Main Flask application (routes, views)
├── utils.py            # Database connection + helper functions
├── settings_store.py   # Per-user settings persisted as JSON (issue #86)
├── requirements.txt    # Python dependencies
├── README.md           # Project documentation
├── .gitignore          # Ignore secrets, venv, cache files
├── templates/          # HTML templates (index.html, flashcards.html, quiz.html)
├── static/             # CSS, JS, images
├── settings/           # Per-identity config JSON (gitignored, created at runtime)
└── uploads/            # Uploaded MHT files (optional)
```
