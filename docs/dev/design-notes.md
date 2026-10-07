# KuantorFlow design notes

The history and reasoning behind each module and table: measurements,
decisions that were reversed, and why a rule exists. Moved verbatim from
`CLAUDE.md` (#550, #566), which keeps each rule and one line of why and
points here. Read the section for the part of the app you are changing.

## The split (#418)

**The split (#418)** — six modules where there was one, in dependency order:
**`web.py`** (the Flask object, the configuration, the identity and
permission helpers, the two spending guards, the SSE frame),
**`icons.py`** (topic and activity icons, registered as Jinja filters),
**`cards.py`** (the deck, everything that reads or writes a card,
`_save_and_log()`, and #406's topic builder), **`rounds.py`** (the games
chassis, the ten rounds and the quiz), **`chat.py`** (Mykola: the agent
import, the `_mykola_agent` singleton, the chat routes and the per-user chat
logs), and **`app.py`**: sign-in, settings, account deletion, `/robots.txt`
and four side-effect imports. It held the keyword gate too until #199. The dependency runs **one way**:
`web.py` ← `icons.py` ← `cards.py` ← `rounds.py` ← `chat.py` ← `app.py`.
**Nothing may import `app.py`**, or the route table comes along and the
split is undone.
`cards.py` moved *before* the chat, reversing the order #418 gives, because
Mykola's card saver is a card save: `chat.py` has to be able to import
`cards`, and a module cannot import one that does not exist yet.
**Two rules keep a move cheap, and both are tested** in
`automation/tests/test_web_is_the_shared_module.py`. A module is reached
*through* its name — `web.is_admin()`, `cards._save_and_log()` — never
through a copy bound at import (#436), because a bare call still answers
correctly and is simply the one site a stub can no longer reach. And
`app.py` imports the feature modules for their **side effects** alone: a
`from rounds import …` means something was left half-moved.
**Moving a block? Compare the routes.** A decorated function's `ast` line
number is the `def`, not the decorator, so a block boundary drawn from it
leaves `@app.route` behind — which then attaches to the *next* function. It
has happened twice: once as a `SyntaxError`, once silently, with
`/mykola/chat` quietly answering with a database test. Count every route,
context processor and `before_request` across all six modules before and
after, and expect the same set.
**`web.py` takes only what two or more feature modules need.** That is the
whole admission rule, and it is what decided the two cases that looked
borderline: the spending guards moved there because `_generation_refusal()`
is asked by #237's reader *and* #406's topic builder, while
`GENERATED_TEXT_KEY` stayed with the round, because only the reader reads it.
A thing with one caller lives with its caller. Without that rule `web.py`
becomes `app.py` under another name, which is the one way this refactor can
fail while every test stays green.
**Callers reach these names through the module** — `web.is_admin()`, never a
copy bound at import (#436). A bare `is_admin()` in `app.py` still answers
correctly and is simply the one call site a stub can no longer reach, which
is why `automation/tests/test_web_is_the_shared_module.py` asserts it: the
failure is silent in both directions, and that file is the only thing that
would notice. The names stay bound into `app.py` as well, for the tests that
call `app_module.is_admin()` rather than patch it.

## `app.py`

**`app.py`** — routes; **no gate** since #199, so every page answers a
visitor who has typed nothing. The keyword was never an authorisation, only
a shared password, and what stands in its place is the per-action ceilings
described below: three pools per paid action (#456), a per-account ceiling
on each (#447), and an account required to write anything at all (#125).
Optional Google OAuth — a sign-in upserts
a row in **`users`** (#148), keyed on Google's `sub` so an email change
updates rather than forks it, and the session carries `id`, the name claims
and `preferred_name`. A dead database still lets the user in, with
`id = None`. `chat._current_first_name()` is the single place that decides what
Mykola calls someone: `preferred_name` → `given_name` → first word of the
display name. `given_name` is used **whole** by design ("Anna Maria" stays
"Anna Maria"); shortening it is the app guessing at a nickname, and
`preferred_name` is the user's own answer to that. `current_settings()` + a
context processor expose settings to templates.

## `parsers.py`

**`parsers.py`** — `lookup_word(word, translator, explanatory_dictionary)`
dispatches to a **licensed** translator + Oxford/Merriam-Webster dictionaries
(call-time resolution so it's mockable). `TRANSLATORS` (#353) is the one
declaration of the translators — slug, label, fetcher *name*, the environment
variable it needs — and the Settings panel, the lookup panel's title and the
dispatch all render from it, so a fifth provider is one entry. The fetcher is
held **by name** and resolved through `Translator.fetch`: storing the object
captures it at import and silently breaks every test that patches a backend.
A provider is offered only where its key is set, read at call time —
`_reachable_activity()`'s rule for #237. With none set there is no translator,
which #349 answers with a dictionary-only card rather than a failure.
`_google_dictionary()` and `_bing_dictionary()` are **retired, not deleted**:
they called endpoints nobody offered us and #348 is both being withdrawn on
one day. They are not in `TRANSLATORS`, so they cannot be chosen or stored,
and dropping them from `settings_store.CHOICES` is what coerces an account
still holding one onto the default instead of stranding it (#352). A dictionary backend returns
**`(definitions, examples)`** (#225): Oxford supplies both from one pass over
its pages, Wiktionary the same from one pass over its sections,
Merriam-Webster wraps to `(defs, {})`. That tuple is the **only
seam** — whatever `_dictionary_backend()` returns is all `lookup_word()` calls,
and it is what the tests stub. Stubbing the definitions-only fetchers
underneath it instead leaves the real ones in the call path and the offline
tests silently hit Oxford. `_fetch_oxford_definitions()` is kept for the
definitions-only contract and for `seed_topics.py --check-oxford`.
**Wiktionary is the third dictionary (#390), and the only one here with
permission.** Keyless like Oxford, so it is always offered; reachable from
PythonAnywhere, which is what #365 left worth having, since Merriam-Webster
is blocked there and Oxford was the deployment's single explanatory source.
It answers from a REST endpoint that returns parsed JSON, so there is no page
to scrape and no homograph probing — and its `partOfSpeech` is capitalised,
which is lowercased in the fetcher because `POS_SYNONYMS` matches on the
label. Its **examples are usage examples and never quotations**, which is a
licence distinction rather than a quality one: Wiktionary writes an editor's
usage example as a `#:` line (`{{ux|en|...}}`) and a quotation from a
published book as a `#*` line with a `quote-book` or `RQ:` template, and the
first is the community's own writing under CC BY-SA while the second belongs
to whoever wrote the book. **The REST endpoint returns only the first kind**
— measured against the wikitext (`thrive`: 3 usage examples, 9 quotations,
3 returned; `reluctant`: 2 and 7, 2 returned) and then across 91 examples on
30 seeded words with nothing quotation-shaped among them. That is observed
behaviour rather than a documented promise, so `WIKTIONARY_QUOTED` drops
anything opening with a year: if that ever changes, the app loses examples
instead of gaining a licensing problem. Fragments go too — `spotless shirt`
is true and useless, and #235 cannot gap a sentence that is not one.
Examples are collected **only for a part of speech that also produced a
definition**, because `lookup_word()`'s Reverso fallback replaces the
definitions alone and Wiktionary examples must not outlive the credit that
covers them.
The licence is also why the text is copied **verbatim** — rewording or
summarising a definition would make the card an *adaptation*, which
share-alike binds, where copying with a credit is what the licence plainly
permits. It is also why a card records **which dictionary wrote its
explanation**: a credit needs something that remembers what to credit, and
once two sources are mixed in one column the rows already there are
indistinguishable forever. `_attach_dictionary_text()` stamps
`explanation_source` on exactly the cards it gives text to, from the provider
that actually answered — which is not always the one that was asked, since
Reverso still answers when the chosen dictionary has nothing.
Examples are **English only**: `examples_ukr`/`examples_rus` come from Reverso
Context, which is IP-blocked from PythonAnywhere. A card is created per part of
speech the **translator** found and gets its text from the part of speech the
**dictionary** found, matched through `POS_SYNONYMS` (#228) — the two providers
use different words for the same thing (`auxiliary verb` / `modal verb`). The
map is applied to **both** sides and only for matching: a card keeps the label
its translator gave it, because that is what the learner sees. A card the
dictionary cannot explain is **kept** — a translation is enough to keep one.
Since #349 the reverse holds too: when **no translator answers**, the cards
are built from the *dictionary's* parts of speech with the translation fields
left empty — an explanation is most of a card's value, and #348 proved both
translators can be down while Oxford is fine. That is the fallback, not the
rule: a translator that answers still decides the cards, and only **both**
halves empty is a failure.
**Every translator's answer passes an alphabet check** (#544),
`checked_translations()` inside `lookup_word()`'s translator loop, so no
provider has to remember it: each comma-separated variant whose **letters**
are not all Cyrillic, or that holds the other language's own letters
(ы ё ъ э in Ukrainian, є ї ґ і in Russian), is **dropped, never repaired**,
and logged as `TRANSLATE-DROPPED`. A model drifts mid-answer now and then:
语言学 in a Ukrainian field, or екзубeрантний with one Latin "e", which looks
right and marks a learner wrong for typing it correctly. **Letters only** --
`цікавий; дивний`, `шума.` and a stress mark are all in the deck and all
right, so punctuation is not policed; ʼ (U+02BC, which Unicode calls a letter)
is let through by name. An answer with nothing left counts as the translator
having found nothing, so the next one is asked (#353).
Also the **notes-upload parsers**
(`parse_notes_preview` dispatches on the extension: `.txt`, `.docx`, `.mht`)
and the **Reverso copy-paste parser** they share — one state machine fed by
a per-format line classifier (colours for `.mht`/`.docx`, layout for plain
text); glued translation terms are split by Claude with a graceful no-key
fallback.

## `seed_topics.py` + `seed_words.py`

**`seed_topics.py` + `seed_words.py`** (#203) — the starting deck: 18 B2–C1
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

## `utils.py`

**`utils.py`** — `save_flashcard()` is the **single DB write path** (every
save route funnels through it), and it skips duplicate `word`+`pos` (#101).
`place_topic()` (#203) is the *only other* way a topic row is born: where
`_get_or_create_topic()` files a topic somebody invented under `Other`, this
places a topic declared in advance in a named section at a given position, and
**moves** an existing topic of the same name rather than duplicating it —
logged as `TOPIC-PLACED`, because it is the one thing the seed does to data
someone else made.
`claim_unowned_topics()` (#394) is the third and it writes no card at all:
it gives the creatorless topics in one section a creator, because
`created_by_user_id` is what #382 reads to decide who may hide a topic, so a
topic with NULL there can never be made private — an ordinary learner is
`denied` (the ownership check comes first) and the admin gets `nobodys`. Only
NULL is ever claimed: another learner's creator is the only thing standing
between their private topic and everybody else's deck, so theirs is reported
and left. `claim_topics.py` is the console front, and it is **not part of a
deploy** — a one-off, idempotent, `--dry-run` first, in `seed_topics.py`'s
shape. `claim_unowned_cards()` (#396) is its other half, and it is needed
because a claimed topic *still* cannot be hidden while its cards have no
author: `set_topic_visibility()` refuses a topic holding other people's
cards and an unowned card counts as somebody else's. `added_by_user_id`
carries three meanings — #127 hides other people's cards, #162 lets only the
owner delete one, #382 reads it for the `shared` refusal — so claiming a card
hands over a permission rather than a label, which is why only NULL is taken
and why the run **reports every other author** instead of skipping them
quietly. Logged one line per topic (`CARDS-CLAIMED`), because fifty-two
card-shaped lines from one console command would drown the day's real events.
`update_flashcard()` is its edit counterpart (#176): ownership is part of the
`UPDATE`, not a check before it, and only the keys **present** in `entry` are
touched — a missing key means "leave it", which is what keeps an editor that
hides a language from wiping it. It also holds #390's one rule about the
credit: **`explanation_source` follows `explanation_en` and never moves on its
own**. A changed explanation takes whatever source the caller vouches for, and
absent means none — an explanation somebody rewrote is their sentence, and a
credit left on it would put Wiktionary's contributors' names on their words.
**`examples_source` is the same rule for the examples, and it is a second
column because the two are edited separately**: one column could only ever be
right about one of them, dropping the credit from a definition nobody touched
or keeping one over a sentence the learner rewrote. Each surface then credits
only the text it shows — `fields` on `_source_credit.html` says which, so
#271, whose whole question is an example and which renders no definition,
reads the examples' credit and ignores the other. The card page shows the
consequence: both halves the dictionary's is one line under the English
block, one half edited puts the surviving credit against the half it still
covers.
The same reason keeps it out of `EDITABLE_FIELDS` (nobody edits a fact about
provenance, and a submitted one would let a form claim an attribution) and out
of `FILLABLE_FIELDS` (filling it beside somebody else's stored explanation
would credit Wiktionary for Oxford's sentence).
`fill_missing_fields()` (#349) is the third writer, and it exists because
#101 has a sharp edge: a card saved during a translator outage could never be
improved, since looking the word up again is exactly what the duplicate rule
refuses. It fills **only empty columns**, **only** from values the new entry
actually carries, and a stored value holding anything always wins — it
repairs gaps and cannot edit, which is what makes it safe to run on every
skipped duplicate rather than on ones somebody has inspected. It is **not** a
save: `_save_and_log()` still returns False and logs `FILL`, because a fill
reported as a save is #308 again.

## `settings_store.py`

**`settings_store.py`** — per-identity JSON config under `settings/`
(`config-default.json` shared by anonymous visitors, `config-<username>.json`
per Google user). `DEFAULTS` is the source of truth; files self-create, are
validated on read/write, and written atomically. **Read-only for anonymous
visitors** (#102). Add a setting = one entry in `DEFAULTS`.

## Who may do what

**Who may do what** — `can_add_cards()` / `add_refusal()` (#125: only an
account writes), `can_delete_card()` / `delete_refusal()` (#162: only your
own), `is_admin()` (#158), `current_block()` / `is_blocked()` (#126: read
live per request, cached in `g`). Every one of them is enforced in the
**route**; the template versions only decide what to grey or hide. A new
write path asks the predicate *and* leans on `_save_and_log()`, which
refuses on its own.

## `applog.py`

**`applog.py`** — the action logs in `logs/` (`cards.log`, `dict.log`,
`parsed_files.log`, #30; `games.log` and `gen_texts.log`, #448; `mykola.log`
for the companion). Card writes go through `cards._save_and_log()`;
**a new save or delete path must log too**. Helpers never raise, so logging
can't break a request — **with one hole the `try` cannot close**: `_write()`'s
own second parameter is named `action`, so passing `action=` (or `name=`)
as a field raises `TypeError` *at the call*, before the body that swallows
errors runs. It shipped for one commit in #448 and took down every refusal
it touched; a field that names a feature is written `feature=`.
**Each file holds what it is named for** (#448). `dict.log` is dictionary
and translator traffic and nothing else — it had also collected paid model
calls and every spending refusal. A `LIMIT` line goes to the log of the thing
refused (`anonymous_limit_hit(..., log=, action=)`): lookups here, generation
to `gen_texts.log`, chat and the recap to `mykola.log`, the upload to
`parsed_files.log`. **Every game round writes one `ROUND` line** to
`games.log` through `rounds._round_played()` — called beside each results
render, **not** inside `_graded_answers()`, which *Odd one out* and *Real or
fake* never reach. *Fill the gap* logs `stage=dealt` with no score at the
deal, and — only if the learner presses *Finish* — a second line at
`stage=self-marked` counting the ticks (#484); a new game is one entry in `GAME_ROUNDS`
and `automation/tests/test_games_and_gen_texts_logs.py` fails until it logs. `KF_LOGS_DIR` redirects the directory (the test
suite points it at a temp dir). A writer with no request behind it —
`set_user_blocked()`, `place_topic()`, `seed_topics.py` — logs *beside the
write* instead, because `_save_and_log()` reads the session and `g`.
**`requests.log` is one line per request** (#555), written by `web`'s
request hooks and never by a view: method, path (no query string), endpoint,
status, ms, `db=` (connections `utils.get_db_connection()` counted against
the request, which is how #554 is judged in production) and the **account
id, never the email**. Every request gets an id (`g.request_id`, returned as
`X-Request-ID`), and applog appends `rid=` to every line written while it
runs, as `app.logger`'s lines do through a filter on Flask's default handler,
so `grep rid=<id> logs/*.log` is one request's whole story. Static files are
not logged. A streamed response's line (`streamed=yes`) is written when the
stream starts, so its `ms` and `db` stop there. A request that raises is still
logged, as a 500, by the teardown hook. The guide's privacy section names it.
**`model_usage.log` is what the AI bill is added up from** (#562). Every log
recorded *that* a paid call happened, but only Mykola's lines (ai_agent's
`mykola.usage`, in `mykola.log`) said what it used, so the four calls this repo
makes itself -- the translator, the generated text, the topic builder and the
notes splitter -- could not be totalled, and the cost figures in *Three pools
per paid action* stayed a model. `applog.model_usage()` now writes one
`MODEL-USAGE` line beside each call, read off the response's `usage`: feature,
model, input/output/cache tokens, stop reason and ms, plus `rid=` inside a
request. The features are the `USAGE_*` constants, written out because a typo
would quietly start a new row in every report. A call that raised writes
nothing (its feature line says it failed); a reply cut off at `max_tokens`
*does* write, since it spent its tokens. `scripts/model_usage_report.py` totals
these and Mykola's lines per day, feature and model, and prices them only from
a `--rates` file -- rates change, so none are in code. Measured on the first
live lookup: about 432 input and 43 output tokens per language, 1.6-2.1 s.

`cards.log` is the **action** log rather than only a card log: `TOPIC`,
`USER-BLOCK`, `ACCOUNT-DELETE`, `PREFERRED-NAME`, and since #161 `MOVE` and
`SETTINGS`. A move is its own action, not an `EDIT changed=topic`, because the
*previous* topic is the whole point and an edit line can only name the
destination. `SETTINGS` records `set=`, `rejected=` and `unknown=` separately:
the store silently replaces an invalid value with the default, so what was
asked for and what stuck are different questions.

## `games.py`

**`games.py`** — the word games (#233). Holds **one declaration**,
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
Since #237 it also owns the **word matcher** the reader and #235 both use:
`word_pattern()` / `find_word()` return the spans where a card's headword
appears in a piece of English, and `mark_words()` cuts a whole text into
`(run, is_word)` segments plus the used/missing lists. It is a **light stem
match, not lemmatisation** — regular inflections only (`resign`→`resigned`,
`apply`→`applies`, `acquit`→`acquitted`), an expression matched whole and
allowed to hold its object ("takes **it** for granted"), and derivations
deliberately *not* matched, because `worker` is not the card `work` and #235
would gap out an answer the card does not have. Built once on purpose: two
implementations would disagree within a month and fail in opposite directions
— #235 showing a sentence containing its own answer, #237 reporting a word as
unused while it is on the screen.
#235 is the second caller: `gap_sentence()` cuts the word out of one of its
own examples and `gapped_example()` finds an example it can be cut from,
returning **None** when it cannot — which is the eligibility rule, since a
sentence shown ungapped hands over the answer. **Every** occurrence goes, not
only the first: one stored example really can hold two sentences using the
word, and gapping one of them would print the answer beside its own blank.

## `templates/deck.html`

**`templates/deck.html`** (#78, generalised by #235) — the flip deck: the
card, its animation, the paging and the per-device animation toggle. It was
all inside `cards.html`, whose comment said the animation is scoped under
`#deck` to stay local to that activity; that stayed true and stopped being
the right shape when a second activity wanted the same deck with different
faces. A page extending it fills `deck_front` / `deck_back`, which are
**`scoped`** blocks — without that keyword Jinja hands them no loop variables
and both faces render empty. Presses on a control inside a face (the speaker
button, #235's checkbox) do not flip the card, which is why the faces may
carry controls at all.

## `textgen.py`

**`textgen.py`** (#237) — the only paid call this repo makes on its own, and
it follows `parsers._split_glued_translations()` rather than Mykola: a module
model constant, a client built at call time, bounded `max_tokens`, and a
`try` that logs its own failure through `applog`. **Not `MykolaAgent`** — that
is a conversation with a system prompt and card context; this is one
stateless call returning prose, and routing it through the agent would put a
generation feature inside the repo that owns the *companion*.
The prompt carries **the bare words and nothing else** — no explanation,
examples or translations — and the learner's free-text line is capped and
collapsed onto one line before it goes in (`clean_preferred_name()` in
ai_agent is the precedent). `max_tokens` is words × 1.5, not 1:1, or a
150-word request stops mid-sentence. **Highlighting is verified, not
requested**: the model is asked for plain prose and `games.mark_words()`
finds the words afterwards, so the page can say which ones actually appeared
instead of claiming a coverage nobody checked. Spending is guarded *before*
the call (#200) by `web._generation_refusal()`, and with no
`ANTHROPIC_API_KEY` the activity is not reachable at all — `_reachable_activity()`
404s it and the tile does not render, the same way `MYKOLA_AVAILABLE=False`
removes the chat widget.

## `topicgen.py`

**`topicgen.py`** (#406) — the second paid call this repo makes, and it
follows `textgen.py` rather than inventing a third pattern: a module model
constant, a client at call time, bounded `max_tokens`, and a caller that logs
its own failure through `applog`. **It proposes and nothing else** — no card,
no topic, no lookup, no database — because the expensive half of #406 comes
*after* the model call, when the learner approves the list. The prompt asks
for **headwords**, singular and uninflected, because #221 is what an
inflection costs: a word with no dictionary entry becomes a card carrying
translations and no explanation, invisible locally because Reverso covers the
gap. `_parse()` is where a model's output stops being trusted — anything that
is not a single alphabetic headword is dropped rather than sent to a
dictionary, and duplicates go with it.
The route does the rest. `_vet_proposal()` spends the **free** checks first
(#389's batched `parsers.wiktionary_pages()`, which is #391's idea arriving
early): a word no lexicon has is flagged **and unticked**, while a word the
deck already holds is flagged and **left ticked** — #101 is per word *and*
part of speech, so a deck holding `tip` the noun still gains the verb, and
unticking it would be the screen claiming something it does not know.
`claim_word_lookups()` then takes the whole batch **all or nothing** before
the fill opens; a partial claim is exactly the half-built topic the ceiling
decision exists to avoid, and a session write after the first byte of a
stream never reaches the browser. The fill is **SSE**, reusing
`/mykola/chat/stream`'s headers — `seed_topics.py` already says twenty words
is not a ten-second script — and every card is committed as it arrives, so a
dropped connection leaves what worked and #101 makes running it again the
whole of the recovery. Every card still goes through `_save_and_log()`.
**#524 grows an existing topic with the same machine**: `topicgen.extend()`
names the topic and its words in the prompt *and* `_parse(exclude=)` drops
them from the answer, `/flashcards/<topic>/add-words` reuses
`_vet_proposal()` and `_proposed_words.html` (#406's approve list, shared),
and the plan carries `extend` so #406's progress page and stream log
`TOPIC-EXTENDED` instead. **`_extend_refusal()` is the one rule** for the
button and both routes: an account, a topic public or yours, and the
**filing check** — a card is filed by *name* and a private namesake wins, so
a public topic opened with `?t=` beside your private one of the same name is
refused rather than misfiled into the private one.

## The games chassis

**The games chassis** — `/games/<slug>` is the picker and `/games/<slug>/play`
a round, dispatched through `GAME_ROUNDS` in `rounds.py`; a game is one entry
there plus one in `ACTIVITIES`. **`/quiz` is a separate endpoint from
`/quiz/<topic>` on purpose** (#250): with both rules on one endpoint `url_for`
must choose between the path converter and a repeated query parameter, and it
picks the converter, making the multi-topic URL unbuildable. The picker is a
plain GET form whose checkboxes are named `topic`, so the round's URL is
shareable and needs no JavaScript to build. **A round grades the questions it
asked**, read back from the submitted field names, because the draw is random
and re-sampling on POST would mark answers against words nobody saw. Since
#416 that happens in **one place** — `rounds._graded_answers()`, which wraps
`games.asked()` and takes the game's own comparison as a callable, because
"correct" really does differ between a typed headword, a stored translation's
comma-separated variants, a shuffled sentence and a generated option. It is in
`rounds.py` rather than `games.py` for the reason #389 put the Wiktionary vet in
`rounds._vetted_pseudowords()`: that module holds pure round logic with no
request and no database in it. The six rounds that call it are exactly the
six that grade an answer against a card row, so #338's per-learner recall is
one write there rather than one per round (it is, since phase 1 — see
`recall_answers` below) — and the two that must never
record anything, *Odd one out* and *Real or fake*, are the two that **cannot**
call it, since neither posts a card id. A new graded game inherits both by
grading through it. Selection
and round length live in the Flask session — a signed cookie with a ~4 KB
ceiling Werkzeug enforces by silently dropping it, so only currently-visible
topic names go in. #237's generated text goes in too, and the thing that
makes it safe is `textgen.max_tokens()`: the longest text the model is
*allowed* to return is 600 tokens, and the measured worst case — 400 words of
deliberately incompressible text, a signed-in identity and an eighteen-topic
selection — serialises to 3.2 KB of the 4 KB. Only the text and its words are
stored; the highlighting is recomputed on each read. Anything larger still
needs somewhere else to live. The round is **post/redirect/get** so a refresh
re-reads the held text rather than paying for a second one, and the held copy
is keyed on the topics, length and instruction that produced it, so changing
any of them offers a fresh text instead of silently showing an old one.

## Mykola widget

**Mykola widget** lives in `templates/base.html`; its Python is `chat.py`
(#418), endpoints `/mykola/chat`, `/mykola/recap`. Its intelligence comes from the `ai_agent` package.
**Agent tools are hosted here**: the agent defines them, this app injects the
callable that touches the database (`card_saver` → `_save_card_from_chat`,
`name_saver` → `_save_preferred_name_from_chat`, ai_agent#62). Injection is
feature-detected in `get_mykola()` so the repos deploy in either order, and a
saver refuses by **raising** — that includes a save skipped as a duplicate
(#308), which used to return quietly and had Mykola confirming a card that
was never written, into a topic it is not in.
**What he knows about the app is injected the same way** (#310):
`MYKOLA_KNOWLEDGE` hands `docs/user-guide.md` to the agent, which indexes it
beside its own knowledge. The guide is the single source — ai_agent
deliberately describes this app nowhere, because the copy it used to keep
drifted until it was answering "why can't I add cards?" from a description
written before #125. So **a feature change that a learner would notice is a
guide change**, and the guide's `###` headings are its retrieval units: one
heading per feature, since a section covering four activities scores too low
on a question about any one of them to be found.
**The Help page renders the same file** (#460) — one file, two surfaces.
`web.USER_GUIDE` is the one declaration of where the guide is, and both
`chat.MYKOLA_KNOWLEDGE` and `/help` read it, so the page and the companion
cannot quietly read different files. `help.html` holds **no help text** —
structure only — for the reason #310 exists. The Markdown is rendered **at
request time** (`app._rendered_guide()`, cached per process on the file's
mtime) rather than committed as generated HTML, which would go stale the day
somebody edited the guide and forgot a script. `markdown` is imported
*inside* that function, so a deploy that misses its `pip install` loses
`/help` alone, which falls back to the PDF, instead of failing at import and
taking every page with it. **`docs/user-guide.pdf` is still a committed
artefact**, offered as a download: regenerate it with
`reports/scripts/md_to_pdf.py` whenever the guide changes. It had gone a
month stale — still telling learners to type the keyword — before #460, and
`automation/tests/test_help_page.py` now fails when any guide heading is
missing from it.
**The privacy notice lives there too** (#90, #56): `## Privacy: what the
site keeps` in the guide, linked from a small *Privacy* link in every page's
footer and from Mykola's sign-in popup. It states facts about the code — what
the activity logs record and for how long (`applog.ROTATE_DAYS` ×
`KEEP_ROTATIONS`), that anonymous chats are never written (#163), what
`delete_account()` removes and what it does not (the activity logs) — so **a
change to any of those is a change to that section**, and
`automation/tests/test_privacy_notice.py` pins the ones that can be derived.

## A topic's word list

**A topic's word list** (#496) — `/flashcards/<topic>/word-list` (a
standalone print page, the worksheet's shape) and `…/word-list.csv`. Both
read through `cards._topic_and_cards()`, **the topic page's own read**, so
an export can never hold a card the page would not show: #382's 404 and
#127's owner filter come with it. The printout follows the hidden languages;
the CSV keeps every column, is UTF-8 **with a BOM** (Excel on Windows needs
it for Cyrillic) and defuses cells a spreadsheet would run as a formula,
since card text is learners' writing. Wiktionary's text carries its credit in
both (#390): a dagger and a footer line on paper, the `*_source` columns in
the file. Logged as `EXPORT` in `cards.log`; nothing is written or spent.

## `schema.sql` + `apply_schema.py`

**`schema.sql` + `apply_schema.py`** — `schema.sql` holds `CREATE TABLE` only
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
backfill is the first). A brand-new **table** needs no migration at all — the
`schema.sql` pass creates it on an existing database too — which is why
#237's `text_generation_usage` is one edit rather than two.

## `text_generation_usage`

**`text_generation_usage`** (#237) — generated texts per day: one row per
account, plus the row whose `user_id` is **0** counting everybody. Zero
rather than the NULL the ticket described, because MySQL treats NULLs in a
unique key as distinct — an upsert against a NULL `user_id` inserts a fresh
row every time instead of incrementing the first, and a `PRIMARY KEY` cannot
hold NULL at all. It has **no foreign key to `users`**, unlike every other
`user_id` in the file: these are day-scoped counters rather than attribution,
and one stale row that expires the same day beats another RESTRICT/CASCADE
decision on the account-deletion path (#165). `claim_text_generation()`
copies `claim_anonymous_message()`'s single statement so two workers cannot
both take the last slot, and claims the **account row first** — that way a
learner can spend one of their ten on a day the site is exhausted, rather
than a site-wide slot being burned for somebody already over their own limit.

## `word_lookup_usage`

**`word_lookup_usage`** (#388) — lookups per day, and the one paid path
anybody can reach: `parse_word` asks the translator once per language, and
since #353 that is a licensed API on our own key. `_lookup_refusal()` copies
`_generation_refusal()`'s shape — a session nudge, then the ceilings, claimed
in a single statement — with **one deliberate difference**: the `user_id = 0`
row counts **anonymous lookups only**, where #237's counts everybody. So one
visitor in a loop cannot spend what the people who signed up are allowed to,
which is the failure #199 names for #164's shared ceiling. Each call claims
exactly one row, which is why this needs none of #237's argument about which
ceiling to take first. Guarded **before** the providers, in the index page's
lookup *and* in `/lookup.json` — the edit dialog spends the same money, and
leaving it out would be a hole in the account ceiling rather than a smaller
cap. **A blocked account is refused outright** (#126 drew its line at
writing when a lookup was free scraping; since #353 it is a spend, and #237
already refuses them the other paid activity).

## `action_usage`

**`action_usage`** (#447) — what each identity has spent today, per action,
and the one table the three counters above became. They were the same table
three times, differing only in the name of the counter column, each with its
own near-identical claim statement; #447 needs ceilings on three more
actions, and six copies would have made the repetition the design.
`claim_action(action, user_id, limit, amount=1)` is the whole API, and
`action_used_today()` the free read #406's approve screen needs.
**It replaced them with no data step.** Every query against the three was
`WHERE day = CURDATE()`, nothing read a past day, and no report or admin page
read them at all — so the old rows were left to age out rather than migrated,
and the tables are still in `schema.sql` until a later change drops them. The
one visible effect was on the day of the deploy, when spend already counted
reset to zero because the new table started empty.
**The `action` says whose pool a row is**, which `user_id = 0` could not: it
meant anonymous lookups only in #388 and everybody's texts in #237, and one
table would have collided them into a single counter. Account rows carry a
real id under a bare name (`lookup`, `generate`, `chat`); shared rows sit on
`ALL_ACCOUNTS` under a scoped one (`lookup:anon`, `generate:all`). The names
are written out in `utils.py` rather than built from strings, because a typo
in an action name does not fail — it silently opens a fresh ceiling nothing
has ever counted against.
**Every claim is all or nothing, including a single slot.** `used + 1 <=
limit` is the same test as `used < limit`, so #406's batch is not a second
statement — it is this one with a count in it, and a partial claim is the
half-built topic that ceiling exists to prevent. `ROW_COUNT()` read straight
after the conditional update is what says whether *this* call took the last
slot rather than somebody else.

## The gate is gone

**The gate is gone** (#199) — `require_keyword()`, `/enter`, `gate.html`,
`ACCESS_KEYWORD` and the gate's stylesheet block and artwork are all
deleted, and **nothing replaced them in kind**, because a shared password
was never an authorisation: it was one string, handed out by hand, that
every holder could pass on and nobody could revoke. What it actually bought
was a *rate* limit by obscurity, and that is what had to exist first — which
is why #199 shipped last, after #200 (upload needs an account), #388 (the
lookup's ceiling), #447 (per-account ceilings), #456 (three pools per paid
action) and #458 (`robots.txt`). **Limits first, gate second**, with no
window in between where the open internet has an uncapped line to the
Anthropic account.
Two things are now load-bearing that were previously the second of two.
**`SECRET_KEY`** is the whole of the protection on `is_admin()`, because
`email_verified` lives *inside* the signed cookie — #445 is what makes
removing the gate safe, and it is not optional. And **`robots.txt`** stops
describing an intention and starts doing the work, since a crawler can now
reach every path it names.
**Reset Auth (#98) survives as a different thing.** It used to forget two
things, the gate pass and the identity; what is left is the identity plus
what `/logout` does not do — the popup's JavaScript clears this browser's
own storage, which is where Mykola's conversation lives (#170). That is the
reason it is still a separate control rather than a second spelling of sign
out.
**The test suite's `client` fixture no longer enters anything**, and
`fresh_client` is now the same object. Both are kept: several hundred tests
name one or the other, and the distinction they encoded — *inside the gate*
versus *outside it* — is gone rather than inverted.

## `robots.txt`

**`robots.txt`** (#458) — a **real file**, `static/robots.txt`, hand-written
and reviewed in a diff for the reason `seed_words.py` is content rather than
output: generated text cannot be read in a pull request, and this is five
lines that change once a year. A route serves it at `/robots.txt`, because
Flask serves `static/` at `/static/…` and no crawler asks for that.
**The file is the source of truth and the test derives from it** — 
`automation/tests/test_robots_txt.py` parses its `Disallow:` lines and checks
each against the URL map, so a renamed route cannot quietly fall out of the
list while the file goes on looking correct. A second copy in Python would
only ever prove the two copies agree.
**The landing page is indexed and the deck is not.** The deck half is not a
free choice: #194 means learners' uploaded notes become cards, so an indexed
deck is somebody else's material republished at scale — and a cache is the
one part of opening the site that a later commit cannot undo.
It shipped **before** #199 and was exempt from the keyword gate, which was
the only reason shipping it early was worth anything: a crawler will never
have a keyword. Since #199 there is no gate and no exemption, and a crawler
can reach every path it names — which is what turns the `Disallow` list from
a statement of intent into the thing actually keeping the deck out of search
results.
**A request, not a control.** Access is decided by #382's namespace and
#127's owner filter, in SQL; this only decides what turns up in a search.

## Three pools per paid action

**Three pools per paid action** (#456) — yours, everybody-anonymous's, and
everybody's, claimed in that order by `utils.claim_pools()`. Before it the
three features disagreed and **generation was inverted from the other two**:
the only one with a row counting everybody and the only one without a row
counting anonymous traffic, so anonymous texts exhausted what a signed-in
learner drew on — measured, twelve anonymous texts and the thirteenth
request from an account that had spent nothing was refused. Lookup and chat
had the opposite gap: no ceiling at all on the total bill.
**The gap between the anonymous pool and everybody's is the whole
protection.** Anonymous visitors claim their own row *and* the shared one, so
they do spend it — what stops them emptying it is that their own ceiling is
smaller, and once reached the shared row stops advancing with them. Set the
two equal and the bug is back in a new hat, which is why every
`*_ANON_DAILY` here is well below its `*_ALL_DAILY`.
**The numbers are cost-weighted, not uniform.** `ai_agent` answers with
`claude-opus-5`; every call this repo makes itself uses Haiku. One chat
message therefore costs roughly **thirty times** one generated text, so the
chat ceilings are the tight ones and `GENERATION_DAILY_LIMIT` can be
comfortable. `CHAT_ALL_DAILY` is the single number bounding this app's bill.
At the current defaults, every pool emptied every day comes to roughly
$8/day, and **about 70% of that is the two Opus paths** — so the highest-
leverage change available is not a ceiling at all, it is Mykola's model.
`account_refusal()` covers the two account-only actions (the recap and the
notes upload — neither is reachable without signing in); `chat_refusal()`
covers all three chat pools and returns `_generation_refusal()`'s shape, so
an exhausted **anonymous** pool still offers a sign-in while an exhausted
account or site-wide pool says tomorrow.

## Per-account ceilings

**Per-account ceilings** (#447) — `CHAT_USER_DAILY` (40),
`RECAP_USER_DAILY` (5) and `UPLOAD_USER_DAILY` (10), all `_int_env` and 0 to
disable, claimed through `web.account_refusal()`. Mykola's chat, the
welcome-back recap and the notes upload had **no account ceiling at all**:
signing in removed the limit rather than raising it. That was right while the
keyword gate was on — a signed-in visitor was by construction somebody handed
a keyword who then chose to sign in — and #199 has since removed the premise
along with the gate. A Google
account is a cost barrier rather than a bot barrier, so the exemption was
worth what an account costs to buy.
**Each guard sits after every free refusal and immediately before the call it
pays for**, and that ordering is the easy thing to get wrong: the recap's
farewell answer (ai_agent#39) is deterministic, so claiming before it would
spend a slot on a sentence the model never writes. The upload claims beside
#125's refusal and before `file.read()`, for #200's own reason — which file
calls Claude cannot be known without parsing it, and parsing is the thing
being paid for.
The chat claims in `_mykola_chat_inputs()` rather than in a route, so the
widget's POST, ai_agent's `/api/chat` and the SSE stream share one allowance
and a streamed message does not cost two.
**One message is capped too** (#564): `web.MAX_CHAT_MESSAGE_CHARS` (2,000,
`_int_env`, 0 to disable), checked in the same function **before** either
quota, because a refusal for length is free and must not spend a message.
The 1 MB request cap bounds the question and the history *together*, so
until then one pasted message of close to a megabyte went to Opus counted as
one message. Characters, not bytes, so Cyrillic gets the same allowance. The
widget's `maxlength` comes from the same constant (`mykola_message_max`), so
a learner meets the limit while typing; standalone Mykola has its own
`agent.MAX_QUESTION_CHARS` at the same value.
A refusal carries `sign_in_required: False` — they already signed in, so
there is nothing to offer and the answer is tomorrow.
**The recap happens only when asked** (#495). It is `claude-opus-5` over up
to 12,000 characters of past chats, and it used to fire by itself on every
chat open and on every return after `restart_chat_interval` — the second
path claiming no ceiling at all. Now `/mykola/recap` with `requested: true`
(the *Recap our last chats* button) is the only way to the model; without it
the endpoint answers only the free farewell (ai_agent#39) or nothing. The free
answers come first — farewell, then *nothing to recap* — and the ceiling is
claimed immediately before the call. A requested recap that cannot be given
answers a fixed `notice` sentence rather than silence, because somebody is
waiting on a button. `/mykola/restart-check` still restarts a stale chat (free)
and sends `"recap": null` for old widgets; `_restart_recap()` and
`_last_exchanges()` are gone with it.
With `stream: true` and an agent that has `stream_recap()` (ai_agent,
kuantorflow#495) the recap is **streamed** — `delta`s, then a `done` carrying
`recap`/`notice`/`retry` — and the widget types it through the chat's own
`readReply()`, handed `showRecap()` as its finisher; an older agent gets the
JSON path. `fast` reaches the recap through `_agent_kwargs()` like every
setting, so fast thinking shortens it. `retry` rides only with
`RECAP_FAILED`, and it is what brings the hidden button back.

## `recall_answers`

**`recall_answers`** (#338, phase 1) — every answer a signed-in learner gave
in a graded round, **appended and never updated**. It is a log rather than a
state table because every mature spaced-repetition system keeps one — Anki's
`revlog`, FSRS's `ReviewLog` — and derives the schedule from it: #479's
`recall_schedule` is a cache of this table, rebuildable from it, so changing
the algorithm later loses nobody's progress. Being append-only it has no race
to design around, which is why it needs none of `_claim()`'s single-statement
care.
**Memory attaches to the word and part of speech**, not the card (the
18 September decision on #338 was reversed on the 27th, after looking at
Duolingo, which like us has one shared vocabulary for many learners). So
`word`/`pos` are **copied** at answer time: an edited card keeps the history
of what was asked, duplicate cards are one word, and `fk_recall_answers_card`
is SET NULL so a deleted card keeps its history. `fk_recall_answers_user` is
**CASCADE** — the first in this schema — because this is a derived fact about
one person and the user guide promises deleting the account erases it.
**The write is in `rounds._graded_answers()`**, which is why it now takes the
`activity`: the six rounds that grade against a card reach it, and so since
#484 does *Fill the gap*, whose judge is the learner's own tick — *Finish*
posts every card **turned over** (an unflipped card is not an answer), and the
round answers 204 because the page has already counted the score. Its rows
are ordinary rows; what makes them weaker evidence is `Activity.self_marked`,
the **one declaration** #479's schedule reads, rather than a column or a list
of slugs. Nothing else can reach the seam — *Odd one out* posts indexes,
*Real or fake* has no rows. `_record_recall()` writes only for a signed-in learner, records
a blocked one too (private data, not shared content), and **swallows a
failure** after logging it, because a history table must never cost a learner
their results page. One `INSERT` per round, so every row shares one
`UTC_TIMESTAMP()` — that shared instant plus `game` is what a round *is* in
the log, and why there is no table of rounds. The guide's *What the site
remembers about your answers* is the disclosure, and it shipped with the table.
Nothing reads the table yet; #479, #480 and #92 are the readers, in that order.

## `recall_schedule` + `recall.py`

**`recall_schedule` + `recall.py`** (#479) — each learner's SM-2 schedule,
one row per `(user_id, word, pos)`, and **a cache of `recall_answers`, never a
source**. `recall.replay()` is pure — one word's answers in, its schedule out
— so the live refresh after a round and `scripts/rebuild_schedule.py` are the
same arithmetic and cannot drift. The live refresh replays each answered word
from its *whole* history rather than stepping it, which is what makes that
true. It runs in `_record_recall()` **after** the log is committed, in its own
`try`: a lost log row is lost history, a stale schedule row is a cache the
rebuild repairs, so the second must never take the first with it.
SM-2 on verified pass/fail: 1, 6, then `round(interval × ease)`; a fail resets
to 1 day, counts a lapse and takes 0.2 off ease (floor 1.3); a pass leaves
ease alone, so with booleans ease only falls — conservative by design. Four
rules SM-2 lacks, each written out in `recall.py`: **one answer per
learner-day moves the schedule** (the first); **the day is Kyiv's from
04:00** (the log is UTC — `tzdata` is a requirement because Windows has no
zone database, and a fixed UTC+2 is the fallback); **an early pass is
practice** and moves nothing, while an early *fail* is a real lapse — without
that, a word played four days running reaches 37 days untested; and **a
self-marked answer is weaker** (#484): on a day with any checked answer the
first checked one decides, an unticked card is a full lapse, a tick is half
the growth and never past `WEAK_PASS_CAP` (6 days). Which games are
self-marked is read from `Activity.self_marked`, nowhere else.
`pos` is `NOT NULL DEFAULT ''` here though nullable in the log (a primary key
holds no NULL), `ease` is permille (Anki's `factor`), `due_on` is a `DATE`.
Words are grouped by `casefold()` because the key's collation is
case-insensitive — grouping `Tip` and `tip` apart would compute two schedules
and let the second upsert overwrite the first. **Change a rule in `recall.py`,
then run the rebuild**: that is the whole migration story for this table.
**The draw reads it** (#480): `rounds._draw_weight()` turns one learner's
`utils.due_dates()` into a `games.sample(weight=)` callable through
`recall.draw_weight()` — due today and never answered weigh 1, scheduled for
later weighs `NOT_DUE_WEIGHT` (0.2). **Down-weighted, never excluded**: a
topic you know still deals in full. `games.sample()` knows nothing of
schedules (weights arrive as data, Efraimidis–Spirakis keys, picked cards
shuffled so the heaviest are not always first), and a weight of None is the
old uniform draw — which is what an anonymous visitor, a learner with no
answers, and an unreadable schedule all get. Seven rounds use it: every one
whose question is **one card** (the six graded games and *Fill the gap*, which
now draws through `sample()` rather than slicing a shuffle). *Odd one out*
builds questions from four words across two topics with its own generator,
and *Real or fake* draws bare words with no `pos` to key on. The card and the
schedule meet through `recall.word_key()`, the one definition of how a word is
keyed.
**#92 is the second reader: *Review (N due)*.** A review is **an ordinary
round of an ordinary game**, dealt only from the words due today — there is no
review screen, because a self-rated one would produce exactly the evidence the
schedule refuses. `?review=1` marks it and travels in the URL, which is what
gets it through the grading POST: game forms post to their own URL, *Fill the
gap* to `window.location.href`, the quiz through a `self_url` carrying it.
`rounds._round_cards()` is the one deck read of the seven one-card rounds and
filters to due words in a review, on the GET **and** the POST (a word is still
due while it is graded: the refresh runs after). The draw uses
`recall.review_weight()` — one more per day overdue, so the longest-waiting
come first — and nothing here writes: the answers move the dates as any round's
do. `_due_for_review()` counts against the **visible deck**, not the schedule,
so a due word with no card left is never promised; it is a context-processor
*callable* so only the front page, which calls it, pays for the read.
`_schedule()` caches `due_dates()` in `g`, since one review request reads it
twice. `replay_url()` sends a review's *Play again* back to `/review`:
rebuilding the URL from `topics` would name every visible topic, drop the flag
and remember the whole deck as the learner's selection (#342).
**Today's list stays the same all day** (#529): it is the words due **when
the learner-day began**, answered since or not. Grading refreshes the
schedule at once, so reading the current dates emptied the list after one
game. A review reads `_review_schedule()` instead — the schedule with each
word answered today put back to its start-of-day date, which
`recall.start_of_day_dates()` derives by replaying only the answers before
`recall.day_start(today)` (`utils.histories_answered_since()` brings those
words' whole histories). Nothing is stored, and scheduling is untouched: a
second game is never a learner-day's first answer. Words already answered
today are ticked on the page and weigh `REVIEWED_TODAY_WEIGHT` in the draw;
the badge reads *N due* / *L of N left* / *N done today*.
**#337 is the short clock beside it**: *Fill the gap*'s unticked cards come
back in the next round. `_fill_the_gap_marked()` stores their `word_key`s in
the **session** (`games.MISSED_KEY`, capped at `MISSED_CAP`, *replaced* each
finish so a remembered word leaves), for everybody, signed in or not; the
deal splits them off with `games.split_carried()` — at most `CARRY_SHARE`
(half) of the round — draws the rest through `sample()` as usual, and
shuffles the two together. No schema and no conflict with the schedule: repeats
in a sitting are never a learner-day's *first* answer, so they move nothing.
**Its round size has two sources** (#499): the picker's `words` when the URL
carries it, like every game, and *Cards per round* (`gapped_deck_size`,
#235) when it does not — the topic's flashcards page and a review link
straight in with no box. `_gap_round_size()` decides, and *Play again*
carries `words` only if the round came with it. The other games fall back to
the remembered picker number instead, and that difference was kept on
purpose.
**The one browser-only part**: *Play again* waits for *Finish*'s POST (at most
`REPLAY_WAIT_MS`, 1.5 s) before navigating, because the next deal reads what
that POST stored; a plain click is intercepted, a new-tab click is not.

## My progress

**My progress** (#493) — `/progress`, a signed-in learner's own report from
`recall_schedule` and `recall_answers`; nothing new is recorded. **`progress.py`
is pure** (rows in, report out) like `recall.py`, and holds the one
definition of the statuses: **known** is an interval of `KNOWN_INTERVAL`
(7) days or more, **struggling** is `STRUGGLING_LAPSES` (2) lapses *and* not
yet back to a week, so a word that recovered graduates, and **learning** is
the rest. **Due today is a count across them, not a fourth status**, or it
would disagree with the Review button, which counts every due word whatever
its state. `utils.schedule_rows()` / `answer_rows()` filter on the
**session's** id and nothing else, and that clause is the whole of the
privacy; a real-MySQL test proves it by breaking it. The topic filter
narrows every section through the visible deck's word→topics map
(`rounds._word_topics()`, every card's owner, never another learner's
private topic). **The PDF is the browser's** *Save as PDF* (#340's way, no
server library); base.html's #521 print rules drop the chrome, and a
`print-only` block adds what a teacher reading the file needs. The header
link is hidden on a phone, where a fourth link wraps #502's one-row header;
the front page's *My progress* button is the way in there. The student
sends the file, which is why there is no teacher role (#492 is that road).
**A date range** (`from`/`to`, read by `rounds._date_range()`, which ignores
what is not a date, clamps the future to today and turns a backwards range
round) drops the answers outside it first: the words become those
practised in the range, counted by that period's answers, and the activity
table covers it. A word's state is still **today's** — the schedule holds
nothing else, so "how well do they know what they practised this term" is
the question a range answers.

## The Quiz's two directions

**The Quiz's two directions** (#540) — `?dir=from-en` (the English word
shown, its translation typed: the Quiz as it always was, and the default) or
`?dir=to-en` (the translation shown with #270's hint, the part of speech and
first letter, and the English word typed). `rounds._quiz_dir()` keeps the
choice in the **session**, so the Quiz button on any page opens the way the
learner last chose. The other direction is marked with `games.same_answer()`,
the comparison every typed English answer uses (#267), and accepts **any
word in the selection whose translation shares a variant** with the card's
(`_english_accepted()`, through the quiz's own `_answer_variants()`) —
*звільнення* is both *resignation* and *dismissal*, and taking only the drawn
card's word would be #258's failure. The selection, not the deck. Recall is
keyed on word + part of speech, not direction, so both move the same word;
the `ROUND` line carries `direction=`. `self_url` takes keywords (`lang=`,
`dir=`) and keeps `lang` first in the query, as every existing link has it.

## `confirmed_words`

**`confirmed_words`** (#258) — words a learner disputed in *Real or fake* and
a lexicon confirmed. The game invents with a trigram model trained on the
deck, so it sometimes produces real English and marks the learner wrong for
being right; #132's filters ask what the **deck** knows, and the deck knows
~2,500 words. The results page lets the learner challenge, and a confirmation
corrects the score and is remembered — `confirmed_words()` feeds
`games.pseudowords(known=...)`, so a settled word is never offered as
invented again, **for everybody**: whether a word is English is not a
per-user fact. No foreign key to `users` for that reason, and because
deleting an account must not un-confirm English.
`parsers.confirm_word()` is the checker and it is **positive-only**:
Wiktionary first (documented, free, licensed for reuse, and the only one of
the three that had `subrogation`, `replevin`, `laches` and `demurrage`), then
Oxford for its learner-facing definition. Only Wiktionary's *existence* is
read and the learner gets the link, so no CC BY-SA content is reproduced. A
hit is evidence a word is real; **a miss is evidence of nothing** — the two
lexicons miss rare words, which is the whole reason the button exists — and a
failed request is a third answer, never a miss. Nothing in this path may say
"confirmed invented". The ticket's own design chose Google's `dt=bd` lookup;
#348/#353 retired that endpoint, which is why this asks Wiktionary instead.
**#389 asks the same question one step earlier**: before a round is shown,
`parsers.wiktionary_pages()` checks the invented words in **one batched
request** (`titles=a|b|c`, 50 at a time, ~3 KB, ~0.3 s) and the route drops
the hits and tops up from the generator. Measured over 60 rounds of the
production deck, **one round in three offered a real English word as
invented** — `defence`, `provision`, `edition`, `version` and `bailment`
among them — so #258's dispute path was the only thing standing between a
learner and being marked wrong for being right, and it only works if they
notice and press the button. The vet lives in `rounds._vetted_pseudowords()`,
**not** in `games.pseudowords()`, which has no network or database in it —
every filter that function holds is a property of the *deck*, and this one is
a property of English. Rejected words are fed back as `known`, which blocks
their stems too, so a run that offered `defence` does not come back with
`defencive`. An **unreachable lexicon leaves the round playable** with the
words unvetted, which is exactly what the game did before: a game that will
not start is worse than one that is occasionally wrong.
**The vet deliberately writes nothing to `confirmed_words`.** It tests
*existence*, not English, because over-rejecting a French word costs nothing
here — but that table is read back as "somebody already checked, it is real"
and shown to a learner, and of 26 candidates with a Wiktionary page only 17
were English. Filling it from an existence test would have the app vouching
for `concile` on the strength of a Dutch entry.

## `topics`

**`topics`** (#207) — topics are a table, and `flashcards.topic_id` points at
it. `flashcards.topic` is still written alongside, holding the **canonical**
spelling from the topics row: it is what `ai_agent`'s `cards_db` still reads
and the rollback if `topic_id` proves wrong, and a later phase drops it. A
topic name becomes an id in exactly one place — `_get_or_create_topic()`,
reached from `save_flashcard()` and `move_flashcard()` — so every producer
upstream (the parsers, the review popup, Mykola's tool schema) keeps speaking
names, and an unknown name still creates the topic (#177's promise).
`created_by_user_id` **decides who may see a private topic** (#382) and is
attribution everywhere else; #127 still filters on *card* ownership, and the
two are different questions asked with different arguments — `owner_id` is
the preference (None whenever it is off), `viewer_id` is who is asking. Its foreign key is
`ON DELETE SET NULL`, unlike `flashcards`' `RESTRICT` — a topic may hold other
people's cards, so deleting its creator's account must leave it standing.
`get_topics()` still lists only topics that **have cards**; empty topic rows do
occur (delete the last card in one) and are deliberately kept, because the row
is where the name, creator and age live.

## `topics.is_public` + `topics.namespace`

**`topics.is_public` + `topics.namespace`** (#382) — a topic only its creator
(and the admin) can see. `is_public` defaults to true and every existing topic
is public. `namespace` is the second half of `uq_topics_namespace (name,
namespace)`: **0 for a public topic**, so a public name stays unique across
the site, and **the creator's id for a private one**, so each learner may hold
one private `Work` beside the public one. That is what lets private topics
leave the shared namespace *without* a name ceasing to identify a topic
everywhere else — every URL, link and remembered game selection still carries
a name.
The app writes `namespace`, and only because MySQL will not:
`IF(is_public, 0, created_by_user_id)` is refused as a generated column (1215)
and as a CHECK (3823), both because `fk_topics_user`'s ON DELETE SET NULL
needs that column for its referential action. So the *uniqueness* is still the
database's and only the *derivation* is ours — in one UPDATE, in
`set_topic_visibility()`, which is the only writer of either column and
refuses the two flips that cannot work: a topic holding **other people's
cards** (hiding it would take their card out of their own deck) and a
creatorless topic (nobody to own it). Deleting an account makes that account's
private topics public *before* the foreign key nulls their creator, merging
the one name a public topic already holds — otherwise they would be topics
nobody can see and nobody can un-hide, and #165 would fail on them.
Reads take a `viewer_id`/`admin` pair beside #127's `owner_id`;
`resolve_topic()` turns a name into the one topic this visitor means (theirs
first, then the public one) and the topic page **404s** what it cannot
resolve, since a name reaches it from whatever URL somebody kept. The games
need no check of their own: `resolve_selection()` already drops names that are
not visible.

## `topic_sections`

**`topic_sections`** (#215) — topics are grouped, and `topics.section_id`
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
