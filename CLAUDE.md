# CLAUDE.md — KuantorFlow

Guidance for Claude Code (and contributors) working in this repo.

## What this is

KuantorFlow is a **Flask** language-learning web app for Ukrainian- and
Russian-speaking learners of **English**: build bilingual flashcards from
dictionary lookups and note imports, drill them with quizzes and a flip-card
deck, and chat with **Mykola**, an AI study companion. Deployed on
PythonAnywhere (MySQL).

## Three-repo architecture

| Repo | Role |
|---|---|
| **kuantorflow** (this) | The web app: routes, templates, parsers, settings, DB access. |
| **[ai_agent](https://github.com/Kuantor/ai_agent)** | Mykola, the RAG AI companion. **Imported, never duplicated** — `app.py` adds it to `sys.path` (`AI_AGENT_PATH`, default sibling `../ai_agent`) and imports `MykolaAgent`. If missing, `MYKOLA_AVAILABLE=False` and the widget just doesn't render. |
| **[kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)** | The pytest suite + DB backup + maintenance scripts. Tests live **there**, not here. |

## Run locally

```bash
venv/Scripts/python app.py      # http://localhost:5000
```

Needs a gitignored `.env` (see `.env.example`): `SECRET_KEY`, `DB_*` (MySQL),
`ACCESS_KEYWORD` (the keyword gate), and optionally `GOOGLE_CLIENT_ID/SECRET`
(sign-in). The local venv is Python 3.14.

## Key modules & patterns

- **`app.py`** — routes; a keyword **gate** (`before_request`) blocks every
  page until the keyword is entered; optional Google OAuth (identity in the
  **session cookie only**, never persisted); `current_settings()` + a context
  processor expose settings to templates.
- **`parsers.py`** — `lookup_word(word, translator, explanatory_dictionary)`
  dispatches to Google/Bing translators + Oxford/Merriam-Webster dictionaries
  (call-time resolution so it's mockable). Also the **notes-upload parsers**
  (`parse_notes_preview` dispatches on the extension: `.txt`, `.docx`, `.mht`)
  and the **Reverso copy-paste parser** they share — one state machine fed by
  a per-format line classifier (colours for `.mht`/`.docx`, layout for plain
  text); glued translation terms are split by Claude with a graceful no-key
  fallback.
- **`utils.py`** — `save_flashcard()` is the **single DB write path** (every
  save route funnels through it), and it skips duplicate `word`+`pos` (#101).
- **`settings_store.py`** — per-identity JSON config under `settings/`
  (`config-default.json` shared by anonymous visitors, `config-<username>.json`
  per Google user). `DEFAULTS` is the source of truth; files self-create, are
  validated on read/write, and written atomically. **Read-only for anonymous
  visitors** (#102). Add a setting = one entry in `DEFAULTS`.
- **`applog.py`** — the action logs in `logs/` (`cards.log`, `dict.log`,
  `parsed_files.log`, #30). Card writes go through `app._save_and_log()`;
  **a new save or delete path must log too**. Helpers never raise, so logging
  can't break a request. `KF_LOGS_DIR` redirects the directory (the test
  suite points it at a temp dir).
- **Mykola widget** lives in `templates/base.html`; endpoints `/mykola/chat`,
  `/mykola/recap`. Its intelligence comes from the `ai_agent` package.

## Conventions

- **Tests are in a separate repo.** When you change app behaviour, open a
  **parallel test PR** in `kuantorflow_automation` (a `tests/…` branch),
  alongside the code PR here.
- **Significant PRs get a report** — a Markdown + PDF verification report
  committed under `kuantorflow_automation/test_reports/` (render with
  `reports/scripts/md_to_pdf.py`). Small PRs are exempt unless asked.
- Report tooling lives in `reports/scripts/` (`md_to_docx.py`, `md_to_pdf.py`).
- **Never duplicate `ai_agent` code here** — import it. **Never commit
  secrets**; `.env` and `settings/*.json` are gitignored.

## Deploy (PythonAnywhere)

`git pull` **both** `kuantorflow` and `ai_agent` (siblings), install
requirements into the app venv, reload the web app. Note: Reverso and
Merriam-Webster are blocked from PythonAnywhere's IPs, so those paths fall
back (Google / Reverso alternatives); `ANTHROPIC_API_KEY` lives in
`ai_agent/.env`.
