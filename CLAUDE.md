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
  page until the keyword is entered; optional Google OAuth — a sign-in upserts
  a row in **`users`** (#148), keyed on Google's `sub` so an email change
  updates rather than forks it, and the session carries `id`, the name claims
  and `preferred_name`. A dead database still lets the user in, with
  `id = None`. `_current_first_name()` is the single place that decides what
  Mykola calls someone: `preferred_name` → `given_name` → first word of the
  display name. `given_name` is used **whole** by design ("Anna Maria" stays
  "Anna Maria"); shortening it is the app guessing at a nickname, and
  `preferred_name` is the user's own answer to that. `current_settings()` + a
  context processor expose settings to templates.
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
  `update_flashcard()` is its edit counterpart (#176): ownership is part of the
  `UPDATE`, not a check before it, and only the keys **present** in `entry` are
  touched — a missing key means "leave it", which is what keeps an editor that
  hides a language from wiping it.
- **`settings_store.py`** — per-identity JSON config under `settings/`
  (`config-default.json` shared by anonymous visitors, `config-<username>.json`
  per Google user). `DEFAULTS` is the source of truth; files self-create, are
  validated on read/write, and written atomically. **Read-only for anonymous
  visitors** (#102). Add a setting = one entry in `DEFAULTS`.
- **Who may do what** — `can_add_cards()` / `add_refusal()` (#125: only an
  account writes), `can_delete_card()` / `delete_refusal()` (#162: only your
  own), `is_admin()` (#158), `current_block()` / `is_blocked()` (#126: read
  live per request, cached in `g`). Every one of them is enforced in the
  **route**; the template versions only decide what to grey or hide. A new
  write path asks the predicate *and* leans on `_save_and_log()`, which
  refuses on its own.
- **`applog.py`** — the action logs in `logs/` (`cards.log`, `dict.log`,
  `parsed_files.log`, #30). Card writes go through `app._save_and_log()`;
  **a new save or delete path must log too**. Helpers never raise, so logging
  can't break a request. `KF_LOGS_DIR` redirects the directory (the test
  suite points it at a temp dir).
- **Mykola widget** lives in `templates/base.html`; endpoints `/mykola/chat`,
  `/mykola/recap`. Its intelligence comes from the `ai_agent` package.
  **Agent tools are hosted here**: the agent defines them, this app injects the
  callable that touches the database (`card_saver` → `_save_card_from_chat`,
  `name_saver` → `_save_preferred_name_from_chat`, ai_agent#62). Injection is
  feature-detected in `get_mykola()` so the repos deploy in either order, and a
  saver refuses by **raising** — the agent turns that into an error the model
  relays in character.
- **`schema.sql` + `apply_schema.py`** — `schema.sql` holds `CREATE TABLE` only
  and describes a **fresh** database; every change to an **existing** one is a
  `Step` in `apply_schema.py`'s `MIGRATIONS` (#180). Adding a column is
  therefore two edits: the column in `schema.sql`, and a migration in the
  script. Never leave an `ALTER` in `schema.sql` as a comment — re-applying the
  file can't run it, which is how card saving broke in production on
  2026-08-02. Table order in `schema.sql` is **dependency order**: a foreign key
  needs its target created first, which is why `users` and `topics` sit above
  `flashcards`. Most steps are idempotent because the object they create can be
  looked up by name; a step that moves **data** carries a `Pending` probe
  instead — SQL that still returns rows while there is work left (#207's topic
  backfill is the first).
- **`topics`** (#207) — topics are a table, and `flashcards.topic_id` points at
  it. `flashcards.topic` is still written alongside, holding the **canonical**
  spelling from the topics row: it is what `ai_agent`'s `cards_db` still reads
  and the rollback if `topic_id` proves wrong, and a later phase drops it. A
  topic name becomes an id in exactly one place — `_get_or_create_topic()`,
  reached from `save_flashcard()` and `move_flashcard()` — so every producer
  upstream (the parsers, the review popup, Mykola's tool schema) keeps speaking
  names, and an unknown name still creates the topic (#177's promise).
  `created_by_user_id` is **attribution only**: nothing reads it to decide
  permissions, and #127 still filters on *card* ownership. Its foreign key is
  `ON DELETE SET NULL`, unlike `flashcards`' `RESTRICT` — a topic may hold other
  people's cards, so deleting its creator's account must leave it standing.
  `get_topics()` still lists only topics that **have cards**; empty topic rows do
  occur (delete the last card in one) and are deliberately kept, because the row
  is where the name, creator and age live.

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
requirements into the app venv, run **`python apply_schema.py`** (idempotent —
it prints what it changed and what was already in place; `--dry-run` to look
first), reload the web app. Note: Reverso and
Merriam-Webster are blocked from PythonAnywhere's IPs, so those paths fall
back (Google / Reverso alternatives); `ANTHROPIC_API_KEY` lives in
`ai_agent/.env`.

**`apply_schema.py` is the only schema step, and it is always the same two
commands** — from a Bash console, in the `kuantorflow` directory, with the app's
venv active:

```bash
python apply_schema.py --dry-run   # read this first
python apply_schema.py             # then apply
```

Reload the web app afterwards. Re-running is safe and says `nothing to do`.

Read the dry run before applying, and expect one line per step. A `+` line is a
change, `=` is already in place, `~` is pending under `--dry-run`. If a step
fails the script stops there, prints the statement that was rejected, and tells
you that nothing after it was applied — fix the cause and re-run, rather than
running the remaining statements by hand. Do **not** pipe `schema.sql` into
`mysql`: it cannot alter an existing table and skips every migration, which is
the failure #180 exists to prevent.

For the #207 deploy specifically, the dry run should list `topics`,
`flashcards.topic_id`, `flashcards.topic_id backfill`, `flashcards.idx_topic_id`
and `flashcards.fk_flashcards_topic`. The backfill rewrites `flashcards.topic`
to the canonical spelling of the topic it links to, which can change the **case**
of a topic name where two spellings existed ('Work' and 'work' were already one
topic to every query, but only one row survives). Worth a look before applying:

```bash
python -c "from utils import get_db_connection; c=get_db_connection(); u=c.cursor(); u.execute('SELECT COUNT(DISTINCT CAST(topic AS BINARY)), COUNT(DISTINCT topic) FROM flashcards WHERE topic IS NOT NULL'); print('exact spellings / distinct topics:', u.fetchone())"
```

Equal numbers mean no topic differs only by case and nothing will be renamed.
If they differ, the surviving spelling is whichever the engine groups to, so
decide deliberately rather than after the fact.
