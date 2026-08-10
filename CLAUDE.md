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
| **[kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)** | The pytest suite + DB backup + maintenance scripts. Tests live **there**, not here. Checked out one level deeper than a sibling — `../automation/kuantorflow_automation` — so `../kuantorflow_automation` finds nothing. |

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
  (call-time resolution so it's mockable). A dictionary backend returns
  **`(definitions, examples)`** (#225): Oxford supplies both from one pass over
  its pages, Merriam-Webster wraps to `(defs, {})`. That tuple is the **only
  seam** — whatever `_dictionary_backend()` returns is all `lookup_word()` calls,
  and it is what the tests stub. Stubbing the definitions-only fetchers
  underneath it instead leaves the real ones in the call path and the offline
  tests silently hit Oxford. `_fetch_oxford_definitions()` is kept for the
  definitions-only contract and for `seed_topics.py --check-oxford`.
  Examples are **English only**: `examples_ukr`/`examples_rus` come from Reverso
  Context, which is IP-blocked from PythonAnywhere. A card is created per part of
  speech the **translator** found and gets its text from the part of speech the
  **dictionary** found, matched through `POS_SYNONYMS` (#228) — the two providers
  use different words for the same thing (`auxiliary verb` / `modal verb`). The
  map is applied to **both** sides and only for matching: a card keeps the label
  its translator gave it, because that is what the learner sees. A card the
  dictionary cannot explain is **kept** — a translation is enough to keep one.
  Also the **notes-upload parsers**
  (`parse_notes_preview` dispatches on the extension: `.txt`, `.docx`, `.mht`)
  and the **Reverso copy-paste parser** they share — one state machine fed by
  a per-format line classifier (colours for `.mht`/`.docx`, layout for plain
  text); glued translation terms are split by Claude with a graceful no-key
  fallback.
- **`seed_topics.py` + `seed_words.py`** (#203) — the starting deck: 18 B2–C1
  topics × 20 words, turned into cards by the app's own `lookup_word()` and
  `save_flashcard()`. **Optional and idempotent**; not part of a deploy.
  `seed_words.py` is **content in version control**, never generated at run
  time — a list from a model each run would give local and production
  *different* decks and could not be reviewed in a diff. Its **order is
  load-bearing twice**: it is the lookup order, so an interrupted run leaves the
  useful half, and it becomes `topics.position` in the section. The script runs
  in **two passes** — `place_topic()` files the eighteen into `B2–C1
  Conversational Topics` numbered from 1 *first*, then the cards are saved;
  reversed, `save_flashcard()` would put every one of them in `Other` at 0.
  Output stays **ASCII** (a Ukrainian translation on a cp1252 console raises,
  which would end a run that was saving fine). **Every word has a verified
  Oxford entry** — Oxford is the only explanatory dictionary reachable from
  PythonAnywhere, so a word it lacks reaches production with translations and no
  explanation, and locally you would never notice because Reverso covers the gap
  (that was #221). `--check-oxford` re-asks the dictionary about all 360 and
  exits non-zero naming any it cannot define; run it when changing a word, and
  use Oxford's **headword** (`tactic`, not `tactics`).
- **`utils.py`** — `save_flashcard()` is the **single DB write path** (every
  save route funnels through it), and it skips duplicate `word`+`pos` (#101).
  `place_topic()` (#203) is the *only other* way a topic row is born: where
  `_get_or_create_topic()` files a topic somebody invented under `Other`, this
  places a topic declared in advance in a named section at a given position, and
  **moves** an existing topic of the same name rather than duplicating it —
  logged as `TOPIC-PLACED`, because it is the one thing the seed does to data
  someone else made.
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
  suite points it at a temp dir). A writer with no request behind it —
  `set_user_blocked()`, `place_topic()`, `seed_topics.py` — logs *beside the
  write* instead, because `_save_and_log()` reads the session and `g`.
  `cards.log` is the **action** log rather than only a card log: `TOPIC`,
  `USER-BLOCK`, `ACCOUNT-DELETE`, `PREFERRED-NAME`, and since #161 `MOVE` and
  `SETTINGS`. A move is its own action, not an `EDIT changed=topic`, because the
  *previous* topic is the whole point and an edit line can only name the
  destination. `SETTINGS` records `set=`, `rejected=` and `unknown=` separately:
  the store silently replaces an invalid value with the default, so what was
  asked for and what stuck are different questions.
- **`games.py`** — the word games (#233). Holds **one declaration**,
  `ACTIVITIES`, which the front-page panel, #237's reader button and the topic
  page's activity row all render from: adding an activity is one entry, not one
  entry and three templates. Two fields are load-bearing beyond their names.
  `kind` (`quiz` / `game` / `reader`) decides which panel an activity appears in
  — the quiz keeps its own URLs because `/quiz/<topic>` predates all of this and
  is linked from three templates, while every game shares `/games/<slug>`.
  **`ticket` is present exactly while an activity is a stub** (#253) and is the
  whole lifecycle: the stub page names it, the front-page tile and the topic row
  grey on it (#261), and the test suite reads it (automation#69) — so a game
  ticket drops that one field and every surface follows with no other edit.
  Also the pure round logic, none of which touches the database: `resolve_selection()`
  (repeated `?topic=` parameters, the remembered selection, and "no topic means
  the whole visible deck" are one question, answered once, in page order, with
  names that have since vanished dropped in silence), `word_count()` /
  `sample()`, `scramble()` (#133 — returns **None** rather than an unchanged
  word, because `cat`, `book` and `noon` cannot differ and printing one is
  printing the answer), and `pseudowords()` (#132's n-gram, trained on the whole
  visible deck rather than the selection because a trigram over twenty words
  hands those twenty back).
- **The games chassis** — `/games/<slug>` is the picker and `/games/<slug>/play`
  a round, dispatched through `GAME_ROUNDS` in `app.py`; a game is one entry
  there plus one in `ACTIVITIES`. **`/quiz` is a separate endpoint from
  `/quiz/<topic>` on purpose** (#250): with both rules on one endpoint `url_for`
  must choose between the path converter and a repeated query parameter, and it
  picks the converter, making the multi-topic URL unbuildable. The picker is a
  plain GET form whose checkboxes are named `topic`, so the round's URL is
  shareable and needs no JavaScript to build. **A round grades the questions it
  asked**, read back from the submitted field names, because the draw is random
  and re-sampling on POST would mark answers against words nobody saw. Selection
  and round length live in the Flask session — a signed cookie with a ~4 KB
  ceiling Werkzeug enforces by silently dropping it, so only currently-visible
  topic names go in and a generated text (#237) will need somewhere else.
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
- **`topic_sections`** (#215) — topics are grouped, and `topics.section_id`
  points at the section. Two sections exist: **`Other`**, holding every topic
  that predates the table and every topic created since, and **`B2–C1
  Conversational Topics`**, deliberately **empty** until #203 seeds it.
  `topics.section_id` is nullable only so the column could be added and so a
  topic saved mid-deploy has somewhere to be; in a settled database it is never
  NULL, because the backfill adopted the old topics and `_get_or_create_topic()`
  files new ones under `Other` — so **don't write a "no section" branch**.
  Ordering is `(section.position, topic.position, topic.name)`, and the name
  tiebreak is what makes `position` default to 0: every topic in `Other` holds 0
  and therefore sorts alphabetically, exactly as `get_topics()` always did. A
  section that really is ordered numbers its topics from 1. `fk_topics_section`
  is `ON DELETE RESTRICT`, unlike `fk_topics_user` on the same table — a creator
  is attribution, but a section is *where the topic lives*, so deleting a
  non-empty one has to fail rather than quietly empty it into NULL.
  `get_topics_by_section()` (#218) is what the browse page reads —
  `[(section, [(topic, count), …]), …]`, **every** section including empty ones,
  because a heading is structure. `get_topics()` is deliberately untouched
  beside it: it still answers "which topics are there" for `/topics.json`'s
  move-dialog half, and #178 will keep asking that. `/topics.json` returns
  **both** shapes, and the Mykola widget's `refreshBrowseTopics()` in
  `base.html` renders the grouped one — that JS and `index.html` build the same
  block, so a change to one is a change to both or a chat save silently
  flattens the page.

## Conventions

- **Tests are in a separate repo.** When you change app behaviour, open a
  **parallel test PR** in `kuantorflow_automation` (a `tests/…` branch),
  alongside the code PR here.
- **Significant PRs get a report** — a Markdown + PDF verification report
  committed under `kuantorflow_automation/test_reports/` (render with
  `reports/scripts/md_to_pdf.py`). Small PRs are exempt unless asked.
- Build-time tooling lives in `reports/scripts/` — `md_to_docx.py`,
  `md_to_pdf.py`, and `to_webp.py` (#234), which sizes artwork to the tiles
  and banners. Its numbers are measured against the eighteen topic icons,
  not chosen; `--width` derives height from the source so nothing is cropped.
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

`apply_schema.py` is the only thing a deploy *must* run. **`seed_topics.py` is
not part of a deploy** (#203) — it is a one-off that fills an empty deck, safe to
re-run and safe to skip forever on a database that already has cards. When you do
want it, from `~/kuantorflow` with the app's venv:

```bash
venv/bin/python seed_topics.py --dry-run
```

Read that first: it names any topic it would **move** out of `Other`, which is
the only thing it does to data somebody else made. Then:

```bash
venv/bin/python seed_topics.py
```

Expect it to take a while — 360 words × (two translations + a dictionary), with a
deliberate pause between them. It is resumable: interrupt it and run it again,
and `#101`'s duplicate rule means only what is missing is added. `--topic "Crime
and justice"` does one, `--owner <email>` attributes the deck to an account
instead of leaving it unowned (an unowned deck is invisible to anyone with
`individual_cards` on, #127). Reverso and Merriam-Webster are blocked from
PythonAnywhere, so a run there falls back to Google/Oxford — which is what the
defaults already are.

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

For the #215 deploy, the dry run should list `topic_sections`,
`topics.section_id`, `topic_sections rows`, `topics.section_id backfill`,
`topics.idx_topics_section` and `topics.fk_topics_section` — six steps, and no
`~` against anything from #207. It changes no card and nothing the page renders:
every existing topic moves into `Other` at position 0, which is the alphabetical
order already on screen. Worth confirming afterwards that nothing was left
behind, since the foreign key is what a later section feature will rely on:

```bash
python -c "from utils import get_db_connection; c=get_db_connection(); u=c.cursor(); u.execute('SELECT COUNT(*) FROM topics WHERE section_id IS NULL'); print('topics with no section (want 0):', u.fetchone()[0])"
```
