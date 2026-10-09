# CLAUDE.md — KuantorFlow

Guidance for Claude Code (and contributors) working in this repo. This file is
the **always-loaded core**: what the app is, how to verify a change, and the
rules that hold every time. The reasoning and history behind each rule live in
[`docs/dev/design-notes.md`](docs/dev/design-notes.md), and the deploy runbook
with its per-ticket notes in [`docs/dev/deploy.md`](docs/dev/deploy.md) (#566).
**Read the design-notes section for the module you are about to change** — the
rules below are the summary, not the whole argument.

## What this is

KuantorFlow is a **Flask** language-learning web app for Ukrainian- and
Russian-speaking learners of **English**: build bilingual flashcards from
dictionary lookups and note imports, drill them with quizzes and a flip-card
deck, and chat with **Mykola**, an AI study companion. Deployed on
PythonAnywhere (MySQL).

## Verifying your work

Tests live in **`../automation/kuantorflow_automation`**, with their own venv
(#550). Run them from there:

```bash
cd ../automation/kuantorflow_automation
venv/Scripts/python -m pytest -q tests/test_<x>.py tests/test_<neighbour>.py   # targeted (the default)
venv/Scripts/python -m pytest -q -m "not live"                                 # full offline suite
RUN_DB_ROUNDTRIP=1 venv/Scripts/python -m pytest -q -rs tests/test_<x>_db.py   # real MySQL tier
```

- **Targeted by default:** the new test file plus the neighbours it could
  affect (the page's own tests, `test_stylesheet_parses.py` for CSS,
  `test_help_page.py` for the guide). Anton's rule, 1 Oct.
- **The full offline suite** (~2 min, ~2,700 tests) when shared Python changes:
  `web.py`, `utils.py`, `parsers.py`, the `rounds.py` chassis, `chat.py`'s
  inputs, the schema — or when a change's reach is unclear.
- **The real-MySQL tier when the change *is* SQL**: the offline fakes agree with
  any clause. Without `RUN_DB_ROUNDTRIP=1` every `*_db.py` test **skips
  silently** — "7 skipped" looks green — so keep `-rs` and read the summary.
- **Mykola** (`../ai_agent`) has its own tests, plain scripts:
  `venv/Scripts/python test_<name>.py` in that repo, each printing its checks.
- **Prove each new test fails** (see *Conventions*): one break per piece with
  `venv/Scripts/python tools/prove_fails.py <spec.py> --markdown` in the test
  repo, which restores from the saved text; paste its table into the PR.
- **Browser behaviour is proved in a real browser** — the preview pane
  (`.claude/launch.json`, server `kuantorflow`) or headless Chrome — because the
  suite checks the HTML a route returns, not what a browser does with it.
- **Report:** paste the summary line, and say in the PR which tests ran.
- **Never skip, delete or weaken a failing test to get green.** Fix the code. A
  test changes only when its premise changed, and the PR says why.

## Three-repo architecture

| Repo | Role |
|---|---|
| **kuantorflow** (this) | The web app: routes, templates, parsers, settings, DB access. |
| **[ai_agent](https://github.com/Kuantor/ai_agent)** | Mykola, the RAG AI companion. **Imported, never duplicated** — `chat.py` adds it to `sys.path` (`AI_AGENT_PATH`, default sibling `../ai_agent`) and imports `MykolaAgent`. If missing, `MYKOLA_AVAILABLE=False` and the widget just doesn't render. |
| **[kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)** | The pytest suite + DB backup + maintenance scripts. Tests live **there**, not here. Checked out one level deeper than a sibling — `../automation/kuantorflow_automation` — so `../kuantorflow_automation` finds nothing. |

## Run locally

```bash
venv/Scripts/python app.py      # http://localhost:5000
```

Needs a gitignored `.env` (see `.env.example`): `SECRET_KEY`, `DB_*` (MySQL),
`ANTHROPIC_API_KEY` for the paid features, and optionally
`GOOGLE_CLIENT_ID/SECRET` (sign-in). **The site is open** (#199): no keyword;
what bounds it is a daily ceiling on every paid action. The local venv is
Python 3.14.

## The rules that hold every time

Each item names its section in [`docs/dev/design-notes.md`](docs/dev/design-notes.md).

Its sections, in order: *The split (#418)*, *`app.py`*, *`parsers.py`*,
*`seed_topics.py` + `seed_words.py`*, *`utils.py`*, *`settings_store.py`*, *Who
may do what*, *`applog.py`*, *`games.py`*, *`templates/deck.html`*,
*`textgen.py`*, *`topicgen.py`*, *The games chassis*, *Mykola widget*, *A
topic's word list*, *`schema.sql` + `apply_schema.py`*,
*`text_generation_usage`*, *`word_lookup_usage`*, *`action_usage`*, *The gate
is gone*, *`robots.txt`*, *Three pools per paid action*, *Per-account
ceilings*, *`recall_answers`*, *`recall_schedule` + `recall.py`*, *My
progress*, *The Quiz's two directions*, *`confirmed_words`*, *`topics`*,
*`topics.is_public` + `topics.namespace`*, *`topic_sections`*.

**The module split** (*The split (#418)*). Six modules in one dependency
direction: `web.py` ← `icons.py` ← `cards.py` ← `rounds.py` ← `chat.py` ←
`app.py`. **Nothing imports `app.py`**; `app.py` imports the feature modules for
their side effects only. `web.py` takes only what **two or more** feature
modules need; a thing with one caller lives with its caller. **Reach names
through the module** — `web.is_admin()`, `cards._save_and_log()`, never a copy
bound at import (#436) — or a stub can no longer reach the call.
`test_web_is_the_shared_module.py` enforces both. **Moving a block? Count every
route, context processor and `before_request` before and after**: an `ast` line
number is the `def`, not the decorator, and a decorator left behind attaches to
the next function — twice already.

**Writing data** (*`utils.py`*). `utils.save_flashcard()` is the **single write
path** and skips a duplicate word + part of speech (#101). `update_flashcard()`
touches only the keys present and puts ownership in the `UPDATE`;
`fill_missing_fields()` (#349) fills **only empty** columns and is not a save. **`explanation_source` follows
`explanation_en`**, and `examples_source` the examples (#390): an edited text
loses the credit, and neither is editable or fillable. The claim scripts take
**only NULL** owners and report everybody else's. Who may do what
(*Who may do what*) is enforced **in the route**; templates only grey or hide.

**Logging** (*`applog.py`*). A new save or delete path **must log**. Helpers
never raise — except that passing `action=` or `name=` to `_write()` raises
`TypeError` at the call, so a feature field is written `feature=`. **Each log
file holds what it is named for** (#448); a `LIMIT` line goes to the log of the
thing refused; every game round writes one `ROUND` line. **`requests.log`** is
one line per request from `web`'s hooks (#555): path without the query, `db=`
connections, the **account id, never the email**, and `rid=` on every line the
request writes. **Every paid model call this repo makes writes a `MODEL-USAGE`
line** to `model_usage.log` (#562): feature (`applog.USAGE_*`), model, tokens,
ms. A fifth paid call gets a constant and the line; `scripts/model_usage_report.py`
totals it with Mykola's usage lines.

**Paid calls and their ceilings** (*Three pools per paid action*, *Per-account
ceilings*). Every paid call claims its ceiling **after every free refusal and
immediately before the call**; claims are all or nothing (`utils.claim_action()`,
`claim_pools()`), three pools per action, and every `*_ANON_DAILY` stays well
below its `*_ALL_DAILY`. Mykola answers on Opus, everything this repo calls on
Haiku, so the chat ceilings are the tight ones. One chat message is capped at
`web.MAX_CHAT_MESSAGE_CHARS`, checked before either quota (#564). **The recap
happens only when asked** (#495). With no `ANTHROPIC_API_KEY` a paid activity is
not reachable at all.

**Translators and dictionaries** (*`parsers.py`*). `TRANSLATORS` is the one
declaration (#353); fetchers are held **by name** and resolved at call time, so
tests can patch them. A dictionary backend returns `(definitions, examples)`
(#225) and **that tuple is the seam tests stub** — stubbing underneath it lets
the offline suite hit Oxford. **No dictionary fallback** (#526): a word the
chosen dictionary cannot explain gets no explanation, locally as in production.
**Wiktionary text is copied verbatim and
credited** (#390); only its usage examples are used, never quotations. **Every
translation passes the alphabet check** (#544): a variant with a non-Cyrillic
letter, or the other language's own letters, is dropped and logged, never
repaired. Examples are English only.

**Games and recall** (*`games.py`*, *The games chassis*, *`recall_answers`*,
*`recall_schedule` + `recall.py`*). `games.ACTIVITIES` is the one declaration
every surface renders from; `ticket` is present exactly while a game is a stub.
A round **grades the questions it asked**, read back from the field names, in
one place: `rounds._graded_answers()`, which is also the one recall write.
`recall_answers` is **append-only**; `recall_schedule` is a **cache** of it —
change a rule in `recall.py`, then run `scripts/rebuild_schedule.py`. Self-marked
games are read from `Activity.self_marked` and nowhere else. The Flask session
is a signed cookie with a ~4 KB ceiling that Werkzeug enforces by **silently
dropping it**.

**Mykola** (*Mykola widget*). The agent defines tools; **this app injects the
callables** that touch the database, feature-detected so the repos deploy in
either order, and a saver refuses by **raising**. **`docs/user-guide.md` is the
single source** for what Mykola and `/help` know about the app (#310, #460): a
change a learner would notice is a **guide change**, one `###` heading per
feature, and **`docs/user-guide.pdf` is regenerated** with
`reports/scripts/md_to_pdf.py`. The guide's privacy section states facts about
the code (#90) — change the code, change that section.

**Topics** (*`topics`*, *`topics.is_public` + `topics.namespace`*,
*`topic_sections`*). A topic name becomes an id only in
`_get_or_create_topic()`; `flashcards.topic` is still written alongside for
ai_agent. A private topic lives in its creator's namespace and
`set_topic_visibility()` is its only writer. Every topic has a section — **don't
write a "no section" branch**. `index.html` and the widget's
`refreshBrowseTopics()` build the same block: change both.

**Schema** (*`schema.sql` + `apply_schema.py`*). `schema.sql` describes a fresh
database; **a change to an existing one is also a `Step` in
`apply_schema.py`** — two edits. Never leave an `ALTER` in `schema.sql`. A
brand-new table needs no migration. Table order is dependency order.

**Security** (*The gate is gone*, *`robots.txt`*). With the gate gone,
**`SECRET_KEY` is the whole protection on `is_admin()`** (#445), and
`static/robots.txt` is what keeps the deck out of search results (#458) — the
file is the source of truth and its test derives from it.

## Conventions

- **Tests are in a separate repo.** When you change app behaviour, open a
  **parallel test PR** in `kuantorflow_automation` (a `tests/…` branch),
  alongside the code PR here.
- **Prove each test fails before trusting it** (#569). A test written straight
  after the code passes by construction, so nothing in a green run separates
  "catches the bug" from "cannot fail" — #334's dedupe had six of seven pass
  with the fix disabled.
  - **A bug fix is test-first:** write the failing test from the ticket, watch
    it fail, then fix.
  - **A feature is test-first where natural, plus one break per piece**, run
    with `tools/prove_fails.py` in the test repo. Before a feature exists every
    test fails for the same trivial reason (a 404, a missing name), so a vacuous
    assertion stays hidden; only per-piece breaks show what each assertion
    guards (#561 had ten). A break nothing catches is a vacuous test or a wrong
    break, and the tool says so.
  - **Restore from the saved text, never `git checkout --`.** The tool does;
    `git checkout` also throws away uncommitted work in the file, and has
    silently reverted finished work three times here. Committing the fix first
    is still good practice, but no longer the only thing between a break and
    lost work.
- **Significant PRs get a report** — a Markdown + PDF verification report
  committed under `kuantorflow_automation/test_reports/` (render with
  `reports/scripts/md_to_pdf.py`). Small PRs are exempt unless asked.
- **The console one-offs live in `scripts/`** (#442) — `apply_schema.py`,
  `seed_topics.py` + `seed_words.py`, `claim_topics.py`, `claim_flashcards.py`,
  `retopic.py`, `rebuild_schedule.py` (#479), `find_bad_translations.py` (#544, report-only), `model_usage_report.py` (#562, read-only). They are run, never imported by the app, which is why they can
  sit in a directory of their own while the app's modules stay flat in the root
  (see #442 for why *those* have not moved: seven `Path(__file__)` sites that
  would fail **silently** one level down).
  Each one starts with `import _bootstrap`, and **its position is load-bearing**:
  `python scripts/seed_topics.py` puts `scripts/` on `sys.path`, not the repo
  root, so without it the line below — `import utils` — cannot resolve. The test
  suite cannot see that mistake, because `conftest.py` has both directories on
  the path either way; `automation/tests/test_scripts_run_from_their_directory.py`
  runs each script in a subprocess instead, which is the only thing that would.
  `seed_words.py` is the exception and imports nothing at all.
- Build-time tooling lives in `reports/scripts/` — `md_to_docx.py`,
  `md_to_pdf.py`, and `to_webp.py` (#234), which sizes artwork to the tiles
  and banners. Its numbers are measured against the eighteen topic icons,
  not chosen; `--width` derives height from the source so nothing is cropped.
- **Never duplicate `ai_agent` code here** — import it. **Never commit
  secrets**; `.env` and `settings/*.json` are gitignored.

## Deploy (PythonAnywhere)

The full runbook, and every ticket's deploy-day notes, are in
[`docs/dev/deploy.md`](docs/dev/deploy.md). **Read it before any deploy that
touches the schema, a script or a key.** The standing steps:

1. `git pull` **both** `kuantorflow` and `ai_agent` (siblings).
2. Install requirements into the app venv when they changed.
3. `venv/bin/python scripts/apply_schema.py --dry-run`, read it, then without
   `--dry-run`. It is the **only** schema step; never pipe `schema.sql` into
   `mysql`.
4. Reload the web app.

Three facts that matter on every deploy:

- **`SECRET_KEY` is required**, and the app refuses to start without it
  (#445). Changing it signs everyone out once.
- **`ANTHROPIC_API_KEY` belongs in `kuantorflow/.env`** (#412). The agent
  keeps a copy in `ai_agent/.env` for standalone use, so rotating it means
  rotating **both files**.
- **Reverso and Merriam-Webster are blocked from PythonAnywhere**;
  `en.wiktionary.org` and Oxford are reachable.

The console one-offs (`seed_topics.py`, `claim_topics.py`,
`claim_flashcards.py`, `retopic.py`, `rebuild_schedule.py`,
`find_bad_translations.py`) are **not part of a deploy**; each has its command
and its dry run in the runbook.
