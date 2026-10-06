# Deploying KuantorFlow (PythonAnywhere)

The full deploy runbook and the per-ticket notes, moved verbatim from
`CLAUDE.md` (#550, #566). The standing steps come first; each ticket that
needed something extra on its deploy day has its own paragraph after them.

`git pull` **both** `kuantorflow` and `ai_agent` (siblings), install
requirements into the app venv, run **`python scripts/apply_schema.py`** (idempotent —
it prints what it changed and what was already in place; `--dry-run` to look
first), reload the web app. Note: Reverso and
Merriam-Webster are blocked from PythonAnywhere's IPs, so those paths fall
back (Google / Reverso alternatives).

**`SECRET_KEY` is required, and the app refuses to start without it** (#445).
The Flask session cookie is **signed, not encrypted**: the payload is plain
base64 anyone can read, and the signature is the only thing that stops a
visitor writing their own — one claiming they are signed in, that their
address is verified, and that it is the admin's. `is_admin()` requires
`email_verified` (#158), but that field is *inside* the cookie, so every check
that reads the session sits downstream of the signature.

It used to fall back to the literal `dev-secret-change-me`, which is in this
repository — so the signature was worth nothing, and a forged cookie walked
past the keyword gate that then stood, with `is_admin()` answering True. Since
#199 took that gate off, this key is the **only** thing between a visitor and
an admin session rather than the second of two. #274 hardened the same
cookie's `Secure`, `SameSite` and `HttpOnly` flags; those protect it *in
transit*, and none of them applies to a cookie written from scratch.

There is **no fixed fallback**. A configured key is used as given; `python
app.py` alone falls back to a **random** key for that run, with a warning, so
sessions do not survive a restart; every other entry point — WSGI, `flask
run`, a console script, pytest — refuses. Random rather than fixed is the
point: if that check ever misjudged a deployment as local, the failure would
be sessions that do not persist, which is visible and harmless, rather than
sessions anybody can forge, which is neither.

Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`.

**Changing it signs everyone out once** and discards stored chat threads once,
because `_identity_token()` (#170) is a salted digest of it and the widget
compares that against `localStorage`. Both are one-time and neither needs a
migration.

**`ANTHROPIC_API_KEY` belongs in `kuantorflow/.env`** (#412). It used to be read
from `ai_agent/.env`, which worked only because importing the agent loads that
file — so the word lookup, #237's generated text and #406's topic builder all
depended on a repo none of them uses, and a failed agent import took all three
down without a word. `app.py` imports `utils` — which loads this repo's `.env`
— near the top, and the agent import now lives in `chat.py`, which `app.py`
imports **last** (#418). `load_dotenv` does not override what is already set,
so **a value here wins** and the agent's copy is a fallback rather than the
route. Two things would now have to go wrong at once to reverse that, which is
why `automation/tests/test_api_key_route.py` stopped reading the order off
`app.py`'s source and asks the interpreter for it instead — both imports used
to be lines in one file, and they no longer are. Measured both ways: with both files set, this one is used; with
only the agent's set, that one still is — so adding it here breaks nothing and
removing it restores the old behaviour exactly.

Keep the two **in step**: the agent also runs standalone and reads its own
`.env`, so the key is deliberately duplicated rather than moved, and rotating it
means rotating **both files** or the app and Mykola quietly end up on different
keys.

That last point is sharper than it looks, because **Mykola inside the web app
reads the same environment variable the app does** — `agent.py` builds
`anthropic.Anthropic()` with no explicit key, exactly as `textgen.py` and
`topicgen.py` do, and one process has one `os.environ`. So once the key is set
here it is the one Mykola uses *in the app*, since it is already set before his
`load_dotenv` runs. Standalone Mykola — `python agent.py`, `flask_app.py` — never
imports this repo and keeps reading `ai_agent/.env`. Identical keys make that
distinction invisible, which is the point of copying rather than inventing a
second one; **different** keys would have the same companion billing two
accounts depending on how he was started, with nothing anywhere saying so.

**`en.wiktionary.org` is reachable from there** — verified on the deployment
on 30 August, the day #258 shipped:

```bash
venv/bin/python -c "import parsers; print(parsers.confirm_word('bailment'))"
```

answered `{'real': True, 'source': 'Wiktionary', ...}`. That is worth recording
beside the two blocked hosts, because #258's word check degrades *silently and
honestly* when a lexicon cannot be reached — every dispute would come back
"could not check" and nothing would look broken. One line in the repo saves
the next person re-deriving it from an absence.

**`apply_schema.py` is the only schema step, and it is always the same two
commands** — from a Bash console, in the `kuantorflow` directory, with the app's
venv active:

```bash
python scripts/apply_schema.py --dry-run   # read this first
python scripts/apply_schema.py             # then apply
```

Reload the web app afterwards. Re-running is safe and says `nothing to do`.

Read the dry run before applying, and expect one line per step. A `+` line is a
change, `=` is already in place, `~` is pending under `--dry-run`. If a step
fails the script stops there, prints the statement that was rejected, and tells
you that nothing after it was applied — fix the cause and re-run, rather than
running the remaining statements by hand. Do **not** pipe `schema.sql` into
`mysql`: it cannot alter an existing table and skips every migration, which is
the failure #180 exists to prevent.

**`claim_topics.py` is a one-off too** (#394). It gives the topics with no
creator in a section an owner, which is what lets them be made private at all:

```bash
venv/bin/python scripts/claim_topics.py --owner <email> --dry-run
venv/bin/python scripts/claim_topics.py --owner <email>
```

Read the dry run: `+` is a topic it would claim, `=` is one created by somebody
else, which it never takes — for a private topic the creator id is the only
thing deciding who can see it. Re-running says `nothing to do`.

**`claim_flashcards.py` finishes that job** (#396) — the topics' cards:

```bash
venv/bin/python scripts/claim_flashcards.py --owner <email> --dry-run
venv/bin/python scripts/claim_flashcards.py --owner <email>
```

`+` is a topic and how many of its cards have no author; `=` names another
author and their count, which it never touches. Run it after `claim_topics.py`:
a topic needs both before it can be made private.

**`retopic.py` tidies the `Other` section** (#407) — it merges the strays into the
eighteen seeded topics where one fits and renames the survivors, from a plan
declared in the script and nowhere else:

```bash
venv/bin/python scripts/retopic.py --plan pa --dry-run
venv/bin/python scripts/retopic.py --plan pa
```

`--plan local` is the other one; the two databases drifted apart and neither
plan is a subset of the other. **The dry run is a rehearsal, not a prediction**
— it applies every step and rolls back, because the steps are not independent
(the first one creates the topic the next two merge into) and a dry run that
guessed each step against the untouched database called two of them impossible.
`~` is a step that would change something, `+` one that did, `=` one already
done, `!` one skipped and why. A merge that would put the same word and part of
speech in a topic twice is **skipped**, since a bulk UPDATE does not go through
`save_flashcard()` and so does not get #101 for free.

Two things it does that nothing else here does. It writes **both**
`flashcards.topic_id` and `flashcards.topic` for every card that moves — the
string is what ai_agent's `cards_db` reads (#207), so a merge that updated only
the id would leave Mykola naming topics that no longer exist. And it **deletes
the emptied source row**, departing from the rule that an empty topic row is
kept for its name, creator and age: that is right for a topic whose last card
was deleted and wrong for one being consolidated away, where leaving the name in
`uq_topics_namespace` lets the next card saved under it resurrect the row. It
changes no `is_public` and no `namespace`, so a card takes its destination's
visibility and nothing changes hands. Not part of a deploy; re-running says
`nothing to do`.

`apply_schema.py` is the only thing a deploy *must* run. **`seed_topics.py` is
not part of a deploy** (#203) — it is a one-off that fills an empty deck, safe to
re-run and safe to skip forever on a database that already has cards. When you do
want it, from `~/kuantorflow` with the app's venv:

```bash
venv/bin/python scripts/seed_topics.py --dry-run
```

Read that first: it names any topic it would **move** out of `Other`, which is
the only thing it does to data somebody else made. Then:

```bash
venv/bin/python scripts/seed_topics.py
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

**#390 needs `apply_schema.py`, and it is two steps**: the dry run should
show `~ flashcards.explanation_source` and `~ flashcards.examples_source`, and
`=` against everything else. They add two nullable columns and touch no
existing row — every card already there keeps
NULL, which is the honest answer, since the dictionary that wrote those
explanations was never recorded and defaulting them to `oxford` would be
inventing an attribution rather than restoring one. Nothing on any page changes
until somebody looks a word up with Wiktionary selected. No key is needed; it
is offered wherever the app runs.

**#388 needs `apply_schema.py`, and it is one step**: `word_lookup_usage` is a
brand-new table, so the `schema.sql` pass creates it on an existing database
and no migration is needed (#237's shape). The dry run should show
`~ word_lookup_usage` and `=` against everything else. It starts empty, and the
first lookup after the reload writes the day's first row. Nothing else changes
on the day — except that a lookup now has a ceiling, which is the point.
`LOOKUP_ANON_LIMIT`, `LOOKUP_USER_DAILY` and `LOOKUP_ANON_DAILY` tune it from
the environment; 0 turns any of them off.

**#447 needs `apply_schema.py`, and it is one step**: `action_usage` is a
brand-new table, so the `schema.sql` pass creates it on an existing database
and no migration is needed (#237's shape). The dry run should show
`~ action_usage` and `=` against everything else.

Nothing is migrated into it. The three tables it replaces hold day-scoped
counters that nothing reads past today, so their rows are left to age out and
the tables stay in `schema.sql` until a later change drops them. **The one
visible effect is on the day**: spend already counted resets to zero, because
the new table starts empty, so every ceiling is a little looser for the rest of
that day.

**#338 (phase 1) needs `apply_schema.py`, and it is one step**:
`recall_answers` is a brand-new table, so the `schema.sql` pass creates it on
an existing database and no migration is needed. The dry run should show
`~ recall_answers` and `=` against everything else. It starts empty and fills
from the first graded round a signed-in learner finishes after the reload;
**until the step has run, those rounds still work** and each one logs a
"Could not record … for recall" error, which is the thing to look for if the
table stays empty.

**#479 needs three steps, in this order**: `pip install -r requirements.txt`
(it adds `tzdata`), `apply_schema.py` (the dry run should show
`~ recall_schedule` and `=` against everything else — a new table, no
migration), then **`scripts/rebuild_schedule.py`**, `--dry-run` first. The
rebuild is not optional on the day: the live refresh only reaches words answered
*after* the reload, so every answer recorded since #338 shipped has no schedule
row until it runs. Expect one `~` line per learner with answers, all `new`; a
second run says `nothing to do`. It is also the step to run after **any** change
to the rules in `recall.py`.

```bash
venv/bin/python scripts/rebuild_schedule.py --dry-run
venv/bin/python scripts/rebuild_schedule.py
```

**#544 needs no schema step**, and one report afterwards: the saved cards
from before the alphabet check are listed, not fixed, because only a person
knows which letter was meant. Correct each in the card editor on the topic it
names:

```bash
venv/bin/python scripts/find_bad_translations.py
```

**#258 needs `apply_schema.py` too, and it is one step**: `confirmed_words` is
a brand-new table, so the `schema.sql` pass creates it on an existing database
and no migration is needed (the same shape as #237's counter). The dry run
should show `~ confirmed_words` and `=` against everything else. It starts
empty and fills only when a learner disputes a word and wins.

For the **#382 deploy**, pull **ai_agent first** — or at least confirm the
deployed one is #68 or later. Mykola reads the deck through callables the app
injects (`topic_reader`, `card_reader`), which is what makes the chat obey the
same visibility as the page; the injection is feature-detected, so an ai_agent
that predates #68 silently falls back to `cards_db`'s own
`SELECT * FROM flashcards` and a private topic is readable in chat. Nothing
errors, which is why it is worth checking rather than noticing.

The dry run should list three pending steps —
`topics.is_public`, `topics.namespace` and `topics.uq_topics_namespace` — and
`=` against everything else. The third one **replaces** `uq_topics_name`, which
is the only part of this that a rollback cannot undo by reverting code: after it,
two topics may share a name. Nothing on any page changes, because every existing
topic is public and lands in namespace 0. Worth confirming afterwards:

```bash
python -c "from utils import get_db_connection; c=get_db_connection(); u=c.cursor(); u.execute('SELECT COUNT(*) FROM topics WHERE is_public=0 OR namespace<>0'); print('topics that are not plain public (want 0 on the day):', u.fetchone()[0])"
```

For the **#237 deploy**, `apply_schema.py` is needed again after several
pull-and-reload releases: the dry run should list exactly one pending step,
`text_generation_usage`, and `=` against everything else. It creates an empty
counter table and touches no existing row, so there is nothing to check
afterwards beyond the script's own output. #237 also needs `ANTHROPIC_API_KEY`
to be readable by the web app — set it in `kuantorflow/.env` (#412), and without
it the activity simply does not appear.
