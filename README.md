# kuantorflow
Interactive language learning with notes and dictionaries

🌐 **The website is up and running on PythonAnywhere: <https://kuantorflow.pythonanywhere.com/>** (type excapital at the entrance)

KuantorFlow is a flexible language‑learning platform built with Flask.  
It transforms notes, dictionary lookups, and study materials into interactive activities such as explanations, translations, flashcards, and quizzes.

---

## 🚀 Features
- Store words with **English explanations** and optional **Ukrainian/Russian translations**.
- Add **examples in multiple languages** to show real usage.
- Upload notes as **`.txt`, `.docx` or `.mht`** (#137) — plain text, Word
  documents and OneNote exports alike — and convert them into structured
  learning entries. Simple `word — explanation` lines work in every format; a
  Cyrillic right-hand side (`pursuit - преследование`) is stored as a
  translation rather than an explanation.
- **Reverso copy-pastes** (#134/#137): notes that are copy-pastes of Reverso
  dictionary entries are auto-detected and parsed richly — one card per
  word + part of speech (senses aggregated), with English explanation, usage
  examples, and translations. `.mht` and `.docx` are recognised by Reverso's
  colour coding, plain text by its layout. Reverso glues the translation terms
  together with no separators, so Claude splits them back into individual terms
  (multi-word phrases kept intact); without an API key the line is kept whole.
  Plain text lists the terms one per line, so it needs no splitting at all.
  One file may mix Reverso blocks and simple lines.
- Organize flashcards by topic for easy browsing.
- Browse a topic as a **card deck** (#78) — a Quizlet-style activity where one
  card shows at a time, you flip it to reveal the explanation and translation,
  and step through with the Left/Right arrows (or the keyboard). The flip
  animation is scoped to this activity only. Open it from a topic's list view
  via the **Card deck** link (route `/deck/<topic>`).
- Practice with quizzes generated from your saved entries.
- Chat with **Mykola**, an AI study assistant, from a widget in the corner.
- **No duplicates** (#101): a card whose word + part of speech already exists
  anywhere in the database is never saved again — every save path (review
  popup, *Add All*, automatic add, Mykola chat) skips it and tells you the
  word is already present.

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
filesystem-safe — so signed-in users never share preferences. Anonymous
visitors share the default config, which is why it is **read-only for them**
(#102): one incognito visitor must not be able to change settings for every
other anonymous visitor. `POST /settings` requires a signed-in user (403
otherwise), and the popup renders with every control disabled plus a
sign-in hint.

`settings_store.py` is the whole storage layer, and `settings_store.DEFAULTS`
is the source of truth for which settings exist:

| Setting | Default | Introduced for |
| --- | --- | --- |
| `cards_automatically` | `false` | #13 — save looked-up cards without the review popup |
| `translator` | `google` | #20 — `google` or `bing` |
| `explanatory_dictionary` | `oxford` | #20 — `oxford` or `merriam-webster` |
| `show_ukrainian` | `true` | #46 — hide Ukrainian everywhere |
| `show_russian` | `true` | #46 — hide Russian everywhere |
| `quiz_lang` | `ukrainian` | #113 — the language the quiz opens in (`ukrainian` or `russian`) |

Behaviour worth knowing:

- **The files create themselves.** The first read for an identity writes its
  config file with the defaults, so nothing needs to be provisioned on
  deploy — the directory alone is enough. An unreadable or corrupt config
  falls back to the defaults rather than breaking the page (and is never
  silently overwritten).
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

### The Settings popup (#13, #20)

**Settings** in the header opens a popup where each signed-in user edits
their own config file (saved via `POST /settings`, which runs through the
same validation as the store); anonymous visitors see the current defaults
read-only (#102):

- **Add cards automatically** (#13, off by default) — when on, *Look up &
  save* writes the parsed cards straight to the database and shows the usual
  green confirmation banner; when off, the review-before-save popup opens as
  before. Notes uploads always go through review, since editing the parsed
  lines is the point of that popup.
- **Translation (ENG → UKR/RUS)** (#20) — *Google Translate* or *Bing
  Translator*.
- **Explanatory dictionary (ENG → ENG)** (#20) — *Oxford Learner's
  Dictionaries* (default) or *Merriam-Webster*.
- **Visible translations** (#46/#79/#111) — *Show Ukrainian translation* and
  *Show Russian translation* checkboxes (both on by default). Unchecking one
  hides that language everywhere it appears: flashcards (translations and
  examples), the lookup review popup, the quiz (a hidden language can't be
  quizzed on; with both hidden the quiz explains itself), and Mykola's
  answers (he stops offering translations in it — though an explicit request
  in chat still wins). **Hiding is visual only**: lookups keep fetching and
  cards keep storing both languages, so re-enabling a language brings its
  translations back untouched.
- **Quiz language** (#113) — *Ukrainian* (default) or *Russian*: the language
  the quiz opens in. The in-page language switch still lets you take a quiz
  in any visible language — this only sets the default. The toggle is greyed
  out while only one language is visible (the quiz then uses that language
  automatically), and it re-enables live as the checkboxes above change.
- **Restart Mykola's chat after a break** (ai_agent#54) — a slider from 1 to
  24 hours (default **2**). Come back after that long without writing and the
  widget starts a fresh conversation: Mykola reviews your last three
  exchanges, greets you back with a short recap of where you left off, and a
  new chat-log file is opened. The **Never restart chat automatically**
  checkbox beside it stores the interval as `0` and greys the slider out.
  For signed-in learners the break is measured from their newest chat log, so
  it follows them across devices; for anonymous visitors, from the browser
  that holds the conversation.
- **Reset Auth** (#98) — an action button under the settings (an *action*,
  not a setting: it stays enabled for anonymous visitors despite the #102
  read-only freeze). After a confirmation dialog it clears the whole
  session — the gate pass and the Google sign-in — plus the app's own
  browser storage (chat-widget state, consent and welcome flags), landing
  back on the gate. Settings files are untouched: signing back in restores
  your preferences.

### Signed-in identities (#148)

Signing in with Google records a row in the **`users`** table: the account's
Google subject id, email, name, and `preferred_name` — what Mykola calls you.
Anonymous visitors are still never written down.

The row is keyed on Google's **`sub`** claim, not the email. `sub` is unique
per account and never changes, so someone who changes their Gmail address
keeps the same row (and, later, the same cards) instead of appearing as a
second person.

Names come from Google's `given_name` / `family_name` claims rather than being
split out of the display name — splitting guesses wrong on several given names
in a row and on family-name-first locales. When Google supplies no given name,
the first word of the display name is used as a fallback.

Recording the sign-in is best-effort: if the database is unreachable you are
still signed in, with no id, exactly as topic lists and duplicate checks
already tolerate a dead database.

### Only an account may change the database (#125)

Browsing, the card deck, quizzes and word lookups are open to everyone past
the keyword gate. **Writing** is not: adding a card — from the review popup,
the automatic-add path, or by asking Mykola in chat — needs a signed-in
account. An attempt answers with *"Please sign in with Google to make any
changes of the database."* and a working sign-in link, and the looked-up cards
stay in the review popup, so signing in from the prompt leaves them ready to
add.

The refusal lives in the routes, not the buttons: nothing is greyed out, and a
request made by hand is refused exactly the same way. A sign-in whose `users`
row could not be written counts as having no account here — an unowned card
could never be deleted by the person who added it (#162), so the failure is
taken in the safe direction. Deleting was already restricted to your own cards
by #162, and settings to your own file by #102.

### Blocked accounts (#126)

An account can be blocked. A blocked learner keeps everything that is
read-only — flashcards, the card deck, quizzes, word lookups and their own
settings — but cannot add or delete cards, and Mykola's widget is not shown to
them. They are told *"Your account is blocked, so you cannot change the
database. Write to &lt;admin&gt; to ask for access."*, with the address taken from
`ADMIN_EMAILS`, so the message is a way back rather than a dead end. The same
sentence appears in the Settings popup, which is where they will look.

The block is `blocked_at` on their `users` row — a nullable timestamp, so
"blocked?" and "since when?" are one column, and clearing it is the whole of
unblocking. It is **read on every signed-in request**, not stamped into the
session at sign-in: a session cookie lasts 30 days, and a block has to take
effect on the blocked person's next request. Hiding the widget is presentation
only; the chat endpoints refuse a hand-made request themselves.

Blocking is an admin operation, run from the automation repo:

```bash
venv/bin/python maintenance/block_user.py someone@gmail.com --reason "spam in chat"
venv/bin/python maintenance/block_user.py someone@gmail.com --unblock
venv/bin/python maintenance/block_user.py --list
```

The script only calls `utils.set_user_blocked()`, so an admin page — if one is
ever wanted — would share the same implementation. Both actions are recorded
in `logs/cards.log` as `USER-BLOCK` / `USER-UNBLOCK`.

### The providers (#20, #21)

Word lookups go through `parsers.lookup_word(word, topic, translator,
explanatory_dictionary)`, which dispatches to one fetcher per provider
(`_translator_backend()` / `_dictionary_backend()`):

- **Google Translate** — the public `translate.googleapis.com` JSON endpoint
  (`dt=bd` returns dictionary entries grouped by part of speech).
- **Bing Translator** — the same Microsoft Translator engine that powers
  bing.com/translator, reached the way the Edge browser's built-in translate
  feature does: a short-lived anonymous token from `edge.microsoft.com`,
  then the official `dictionary/lookup` API. (bing.com's own web endpoints
  reject non-browser TLS clients, so they can't be used from a server.)
- **Oxford Learner's Dictionaries** — scraped entry pages; one page covers
  one part of speech, so sibling entries (`run_1`, `run_2`, …) linked from
  the page's *Other results* box are fetched too (up to 3 pages).
- **Merriam-Webster** — scraped dictionary page; a single page carries every
  part-of-speech entry.

The dispatch degrades gracefully: a translator backend that fails or returns
nothing falls back to Google Translate, and a dictionary backend that fails
or returns nothing falls back to Reverso's dictionary — a lookup without
definitions is still useful, so definition failures never break a lookup.

---

## 📝 Action logs (#30)

`applog.py` writes a plain-text trail of what the app did into `logs/`
(gitignored — runtime data, not source):

| File | What lands there |
|---|---|
| `cards.log` | cards created, skipped as duplicates, edited, deleted — with the signed-in user's email, or `anonymous` |
| `dict.log` | which translation and dictionary sites were used, how long each took, and every silent fallback |
| `parsed_files.log` | `.txt` / `.docx` / `.mht` uploads: file, size, cards found, and the AI term-split |

Every line is `<timestamp> ACTION key=value …`, so the logs stay greppable:

```bash
grep "word=brittle" logs/*.log
grep "user=anonymous" logs/cards.log
grep DELETE logs/cards.log
```

```
2026-07-27 10:43:33 CREATE word=brittle pos=adjective topic=vocab id=68 langs=ukr source='review popup' user=anonymous
2026-07-27 10:43:30 TRANSLATE word=brittle provider=google lang=uk pos_count=1 ms=107
2026-07-27 10:43:37 PARSE file=LucidDream.txt bytes=403 cards=1 topic=vocab ms=0 user=anonymous
```

Files rotate every **30 days** (Python's timed handler has no calendar-month
unit) and 12 rotations are kept, so a year of history is on disk; the live
file keeps its plain name and rotations gain a date suffix
(`cards.log.2026-08-26`). Archiving them is a separate task. Logging never
breaks a user action — every helper swallows its own errors, so a full or
read-only disk costs a log line, not a saved card. Set `KF_LOGS_DIR` to write
them somewhere else.

---

## 🗄️ Database schema and deploys (#180)

The schema lives in two places on purpose, and one command applies both:

```bash
venv/Scripts/python apply_schema.py            # create/alter whatever is missing
venv/Scripts/python apply_schema.py --dry-run  # just say what would change
```

- **`schema.sql`** describes a **fresh** database and holds `CREATE TABLE`
  statements only.
- **`apply_schema.py`** holds the changes to a database that already
  exists — new columns, indexes and constraints — as an ordered list of real
  statements.

Why the split: `CREATE TABLE IF NOT EXISTS` does nothing to a table that is
already there, so re-applying `schema.sql` adds a new *table* but never a new
*column*. The `ALTER` statements used to sit in `schema.sql` as comments, which
meant a deploy that followed the instructions faithfully still skipped them —
on 2026-08-02 that took card saving down until they were run by hand.

The script is **idempotent**: each step is skipped when the object it creates
is already present, so a re-run reports `nothing to do`, a half-applied
database finishes cleanly, and a failure exits non-zero instead of passing
quietly. Every step is reported either way — `+ applied` or `= already
present` — so a deploy that changed nothing looks different from one that did.

**Adding a column** is two edits: put it in `schema.sql` for databases that
don't exist yet, and add a migration to `MIGRATIONS` in `apply_schema.py` for
the one that does.

---

## 📂 Project Structure

```
kuantorflow/
├── app.py              # Main Flask application (routes, views)
├── utils.py            # Database connection + helper functions
├── schema.sql          # Tables for a fresh database (CREATE TABLE only)
├── apply_schema.py     # Applies schema.sql + pending migrations (issue #180)
├── applog.py           # Action logs written to logs/ (issue #30)
├── settings_store.py   # Per-user settings persisted as JSON (issue #86)
├── requirements.txt    # Python dependencies
├── README.md           # Project documentation
├── .gitignore          # Ignore secrets, venv, cache files
├── templates/          # HTML templates (index.html, flashcards.html, quiz.html)
├── static/             # CSS, JS, images
├── logs/               # Action logs (gitignored, created at runtime)
├── settings/           # Per-identity config JSON (gitignored, created at runtime)
└── uploads/            # Uploaded MHT files (optional)
```
