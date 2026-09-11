# KuantorFlow — Development Report

**Period:** 29 August – 11 September 2026 (two weeks) · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

The previous edition described a week spent being made honest after an outage. These two weeks spent the credit that bought. **The app acquired a lexicon it is actually allowed to use, a ceiling on what it can spend, and a topic that behaves like something a person owns** — and the largest single change, building a whole topic from a sentence, was only possible because the three before it had already been built.

The thread is Wiktionary. It arrived on 31 August as a third explanatory dictionary ([#390](https://github.com/Kuantor/kuantorflow/issues/390)) and it is **the only one of the three with permission**: free, keyless, licensed for reuse, and reachable from PythonAnywhere, where Merriam-Webster is blocked and Oxford had been the deployment's single source of explanations. Its licence is CC BY-SA, which is not a footnote — it is why the text is copied verbatim rather than summarised, and why a card now records *which dictionary wrote its explanation*, in two columns rather than one, because an explanation and its examples are edited separately and a single column could only ever be right about one of them.

That same lexicon then answered two older questions. *Real or fake* invents words with a model trained on the deck, so it sometimes invents real English and marks a learner wrong for being right; [#258](https://github.com/Kuantor/kuantorflow/issues/258) gave them a way to dispute it, and [#389](https://github.com/Kuantor/kuantorflow/issues/389) moved the question one step earlier so the wrong verdict is never shown. The premise was measured rather than assumed, and the number is the reason it was built: across sixty rounds of the production deck, **one round in three offered a real English word as invented** — `defence`, `provision`, `edition`, `version` among them.

Money got its first real ceiling. Since the licensed providers landed, every word lookup spends on our own key, and [#388](https://github.com/Kuantor/kuantorflow/issues/388) put a daily cap on it — deliberately shaped so one anonymous visitor in a loop cannot spend what the people who signed up are allowed to, which is the failure [#199](https://github.com/Kuantor/kuantorflow/issues/199) names about a single shared ceiling.

And topics stopped being labels. They became private-able ([#382](https://github.com/Kuantor/kuantorflow/issues/382)), ownable ([#394](https://github.com/Kuantor/kuantorflow/issues/394), [#396](https://github.com/Kuantor/kuantorflow/issues/396)), tidy-able ([#407](https://github.com/Kuantor/kuantorflow/issues/407) — the `Other` section went from thirteen topics to three locally and seven to three in production), and finally **buildable from a sentence** ([#406](https://github.com/Kuantor/kuantorflow/issues/406)): describe a subject, get a proposed title and word list, check it, and watch the dictionaries fill the cards.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | Wiktionary and its licence, the lookup ceiling, topic ownership and privacy, the topic builder, the edit and review dialogs, the generated text | 23 |
| **kuantorflow_automation** | A parallel test PR for every change, plus two database-tier suites; suite → 1,996 offline | 21 |
| **ai_agent** | No changes; untouched since 17 August | 0 |
| **Total** | | **44** |

One of the 23 is the previous edition's own report PR, so 22 carried product change.

**22 issues closed, all 22 as completed — none closed as not planned.** That is the third edition running with nothing declined, and the previous edition already said this is worth watching rather than celebrating: a backlog where nothing is ever rejected is usually one where nothing speculative is being written down. Unlike the last window, though, these were **not** all filed inside it — [#191](https://github.com/Kuantor/kuantorflow/issues/191), [#231](https://github.com/Kuantor/kuantorflow/issues/231), [#258](https://github.com/Kuantor/kuantorflow/issues/258), [#369](https://github.com/Kuantor/kuantorflow/issues/369) and [#370](https://github.com/Kuantor/kuantorflow/issues/370) had all been waiting, some for weeks. Work was driven by a plan again rather than by what broke.

*All 137 commits across the three repositories were authored by Kuantor (77 in kuantorflow, 60 in kuantorflow_automation, none in ai_agent). **No GitHub Copilot commits were found.** 90 of them carry a `Co-Authored-By: Claude` trailer, which is the assistant's authorship recorded in the commit rather than a second author of the work.*

The regression suite grew from **1,664 to 1,996 passing offline tests**, and to **2,142** with the opt-in database and live-site tests — 332 new offline tests against 22 app changes.

---

## Completed Work by Theme

### 1. A dictionary the app is allowed to quote

[#390](https://github.com/Kuantor/kuantorflow/issues/390) added Wiktionary beside Oxford and Merriam-Webster, and most of the work was not the fetching. It answers from a REST endpoint returning parsed JSON, so there is no page to scrape and no homograph probing — the easy part. The hard part is that its text arrives under **CC BY-SA**, and a licence that asks for attribution needs something that remembers what to attribute.

So a card now records the dictionary that wrote its explanation, in `explanation_source` — and its examples in a **second** column, `examples_source`, because the two halves of a card are edited independently. One column could only be right about one of them: it would either drop the credit from a definition nobody touched, or keep Wiktionary's name on a sentence the learner rewrote. Each surface then credits only the text it is actually showing, which is why the gap-fill game reads the examples' credit and ignores the other entirely.

Two rules fell out of the licence rather than out of taste. The text is copied **verbatim**, because rewording it would make the card an *adaptation*, which share-alike binds, where copying with a credit is plainly permitted. And examples are taken **only where a definition came from the same source**, since the fallback path can replace the definitions alone and an example must not outlive the credit covering it.

The examples themselves needed a distinction the ticket did not have. Wiktionary writes an editor's usage example one way and a quotation from a published book another; the first is the community's own writing under CC BY-SA, the second belongs to whoever wrote the book. Measurement showed the endpoint returns only the first kind — `thrive` has three usage examples and nine quotations, and three came back — but that is observed behaviour rather than a documented promise, so anything opening with a year is dropped. If the endpoint ever changes, the app loses examples instead of gaining a licensing problem.

### 2. The game stopped marking learners wrong for being right

*Real or fake* trains a trigram model on the deck and asks the learner which words are invented. A model trained on English produces English, and the local filters can only ask what the **deck** knows — some 2,500 words, where English has hundreds of thousands.

[#258](https://github.com/Kuantor/kuantorflow/issues/258) shipped the dispute path: the results page lets a learner challenge a verdict, a lexicon settles it, the score is corrected and the word is remembered so nobody is offered it again. The design in the ticket's own comments had chosen Google's word lookup as the checker — an endpoint retired five days earlier as one nobody had licensed to us — so the mechanism was chosen again, and Wiktionary measured *better* than the thing it replaced, including four rare legal terms (`subrogation`, `replevin`, `laches`, `demurrage`) that neither alternative could confirm.

[#389](https://github.com/Kuantor/kuantorflow/issues/389) then asked the same question before the round instead of after a complaint, because the dispute path only works if a learner both notices and presses a button. **Production's dispute table was empty**, which looks like evidence the bug never happens and is equally consistent with nobody reporting it — so the generator was measured directly. Sixty rounds of the production deck produced 269 distinct invented words, **17 of them with an English Wiktionary entry**, landing in one round in three. Four were ordinary B2 vocabulary. One was `bailment`, the word that opened #258 in the first place, still being offered a fortnight later.

The check costs one batched request of about 3 KB and 0.3 seconds per round, because it asks only whether a page exists rather than reading it. That difference also settled a question the ticket had left open: existence is **not** the same as English, and the table that answers a learner "somebody already checked, this is real" is not allowed to be filled from an existence test. Of 26 candidates with a page, only 17 were English.

### 3. The first ceiling on spending

Since the licensed providers arrived, a word lookup is money. [#388](https://github.com/Kuantor/kuantorflow/issues/388) gave it a daily cap in the shape text generation already used — a session nudge, then per-account and site-wide ceilings, claimed in a single statement so two workers cannot both take the last slot.

It differs from the text-generation counter in one deliberate way: the site-wide row counts **anonymous lookups only**. A signed-in learner meets their own ceiling and nothing else. That is the specific failure #199 names about a single shared limit — one visitor in a loop spending the day's budget and every genuine visitor being told to come back tomorrow — and it cannot reach the people who signed up.

The cap is enforced before the providers run and in both places that can reach them, since the edit dialog spends exactly the same money as the front page.

### 4. A topic became something you own

Four changes turned a topic from a text label into a thing with a life cycle.

[#382](https://github.com/Kuantor/kuantorflow/issues/382) made a topic private to its creator. The schema is the interesting part: a second column forms the other half of the unique key, so a public name stays unique across the site while each learner may hold one private topic of that name. MySQL refused to derive that column — rejected as a generated column and again as a constraint, both because the creator foreign key needs it for its own referential action — so the database keeps the *guarantee* and the app keeps the *derivation*, in one statement, in the only function that writes either column.

That immediately exposed a dead end: privacy is decided by the creator, and a topic with no creator can never be hidden by anybody. [#394](https://github.com/Kuantor/kuantorflow/issues/394) and [#396](https://github.com/Kuantor/kuantorflow/issues/396) are the one-off console scripts that claim the unowned topics and their unowned cards — never taking one that belongs to somebody else, since for a private topic the creator id is the only thing deciding who can see it.

[#407](https://github.com/Kuantor/kuantorflow/issues/407) then tidied the section where invented topics accumulate. `Other` had collected thirteen topics locally and seven in production — lower-case names, one-card experiments, and several that the curriculum already covered. Both are now three. The script is declarative, idempotent and reversible in the sense that matters: re-running it says there is nothing to do.

### 5. A topic you can build from a sentence

[#406](https://github.com/Kuantor/kuantorflow/issues/406) is the largest change of the fortnight and it depends on all four above. Describe a subject — *"renting a flat in London, especially the paperwork"* — say how many words you want, and the app proposes a topic name and a list at the deck's level. **Nothing is written and nothing is paid for** until the list is approved.

The approve screen is where the earlier work shows. The free checks run first: #389's batched lexicon call flags a word no dictionary has and unticks it, because a word with no entry becomes a card with translations and no explanation — a defect that once reached production and was invisible locally. A word the deck already holds is flagged and **left** ticked, since duplicate prevention is per word *and* part of speech, and unticking it would be the screen claiming something it does not know.

Then the cost is stated in the learner's own terms — *"12 words, 12 lookups, you have 38 left today"* — and claimed **all or nothing** before the first fetch, so a refusal is a decision read in advance rather than a topic abandoned half-built. The filling itself streams, because twenty lookups is a minute of network work and a page that sits blank for a minute is not an answer; every card is committed as it arrives, so a dropped connection leaves the ones that worked.

### 6. The dialogs that add cards were finished

Eight tickets closed against the edit and review popups, and they read as one arc. [#191](https://github.com/Kuantor/kuantorflow/issues/191) gave the edit dialog a **Look up & update** button that refills a card from the dictionaries — the answer for cards created early or imported from notes, carrying a translation and nothing else, which previously could only be improved by deleting and re-creating them. [#372](https://github.com/Kuantor/kuantorflow/issues/372) put the same button on every card in the notes-upload popup, which forced the shared extraction that made both maintainable.

Around them: [#377](https://github.com/Kuantor/kuantorflow/issues/377) and [#379](https://github.com/Kuantor/kuantorflow/issues/379) marked which parsed cards the deck already holds and made "add it anyway" actually add one; [#380](https://github.com/Kuantor/kuantorflow/issues/380) made the word itself editable before saving; [#384](https://github.com/Kuantor/kuantorflow/issues/384) named the dictionary in the lookup heading rather than only the translator; [#370](https://github.com/Kuantor/kuantorflow/issues/370) and [#375](https://github.com/Kuantor/kuantorflow/issues/375) fixed a clipped focus ring and an action row that fell outside its own dialog on a short window.

[#369](https://github.com/Kuantor/kuantorflow/issues/369) closed the set with a rule rather than a fix: **a dialog that discards work when it closes does not close on a stray click.** Four dialogs were covered when it was written and a fifth — the move dialog — was deliberately left undecided rather than swept in. That decision was made two weeks later and came out on the side of the rule, because the topic field is free text and a stray click can discard a name that exists nowhere else yet.

### 7. Taking a generated text away with you

Three small changes to the reading activity, each reported from use. [#399](https://github.com/Kuantor/kuantorflow/issues/399) removed the two blank lines every generated text opened with — template whitespace made visible by a panel that preserves it. [#401](https://github.com/Kuantor/kuantorflow/issues/401) let a flip card grow to the text on it; a long explanation had been printing outside the card on a phone, and the cause was not the fixed height but an absolutely positioned face that could not size anything. [#403](https://github.com/Kuantor/kuantorflow/issues/403) added copying the passage with its highlighting intact, and printing it.

### 8. Oxford's numbered entries, at last

[#231](https://github.com/Kuantor/kuantorflow/issues/231) was filed as "can and do" and measuring it found **ten of thirty-eight** common words affected — one clean class, words whose senses differ in pronunciation: `lead`, `tear`, `wind`, `row`, `close`, `minute`, `live`, `content`. The fix needed a mechanism the ticket did not have, because every numbered page's related-entries box is empty, so following links can never reach the second entry.

---

## Technical Highlights

**Measure the premise before building the ticket.** #389 was built because sixty simulated rounds against the production deck said one round in three was affected; it would have been built differently, or not at all, on a different number. The same restore-and-measure technique answered #407's real question — whether two merges would collide — before production saw them, and both later ran exactly as rehearsed.

**Never infer production from local.** The two decks have different stray topics, different ids and different card counts. #407 was rehearsed against a downloaded production backup restored into a scratch database, which is also what revealed that every production topic is public and single-owner, so none of local's privacy complications applied there.

**A dry run should be a rehearsal, not a prediction.** #407's first version checked each step against the untouched database and declared two of thirteen impossible — the plan's first step *creates* the topic the next two merge into. It now applies every step and rolls back, so what it prints is what the database did.

**Do the free checks before the paid ones.** A lexicon that costs nothing can reject a word before a licensed API is asked about it. That one idea is now load-bearing in two features, and the tool #389 built for the game was reused unchanged by #406 a week later.

**Keep the guarantee in the database and the derivation in the app.** #382's unique key is enforced by MySQL; only the value feeding it is computed by Python, because the engine refused every way of deriving it.

---

## Lessons Learned

**1. A page nobody can reach is not a feature.** #406 shipped with complete routes, templates and twenty-three passing tests — and **nothing linking to it**. It was usable only by typing the URL. Every assertion was about the page and none about whether a learner could find it, which is a gap a green suite cannot show. It was reported within minutes of being merged. Three tests now cover the entry point itself.

**2. An opt-in test tier rots silently, and this one had.** Gathering the suite figures for this report ran the database tier, which a default run deselects — and found a failure. The test that checks what the schema script creates held a **hand-written list of six tables**; the schema has eight. Both additions happened *inside this window* and neither reached the assertion. Two weeks of green runs never saw it, because the only test watching the one thing a deploy must run was the one test nobody was running. The list is now derived from the schema file so it cannot rot again ([automation#144](https://github.com/Kuantor/kuantorflow_automation/pull/144)).

**3. An empty table is not evidence that nothing is wrong.** Production's dispute table had no rows, which reads as "the game is not making mistakes" and is equally consistent with "nobody presses the button". Only measuring the generator directly could tell those apart, and the answer was one round in three.

**4. Existence is not the same as truth.** #389's check asks whether a lexicon has a page, which is cheap and slightly wrong — nine of 26 hits were entries in other languages. That inaccuracy is free when the cost of over-rejecting is asking the generator for another word, and unacceptable in the table that tells a learner "this is real English". The same data can be good enough for one purpose and not for another, and the ticket's own leaning had to be reversed on that basis.

**5. Check what a control *does* before rewording it** — carried from the previous edition and earned again. #369's move dialog was left undecided on the reasoning that it "holds one typed topic name". Looking at the field showed it is free text that creates a topic if the name is new, which is further from "nothing" than the ticket's framing suggested.

**6. Verification reports: none.** The previous three editions raised this, and the last one said the trend was downward rather than flat. **Zero verification reports shipped in these two weeks, against 22 product pull requests.** `CLAUDE.md` exempts small PRs; it does not exempt a schema change, a licensing decision or the largest feature of the fortnight. This is now the fourth edition reporting it and the first where the count is nothing at all.

---

## Plans for Next Week

**Blockers.** No demo blockers remain. [#199](https://github.com/Kuantor/kuantorflow/issues/199) is still the only launch blocker of any kind — what has to be limited before the keyword gate comes off — and two of the three things it asks for now exist: a lookup ceiling (#388) and a generation ceiling (#237). What remains is the gate itself and its product decisions.

**The obvious next pieces**, both specified and both cheap now that the mechanism exists:

| Ticket | |
|---|---|
| [#391](https://github.com/Kuantor/kuantorflow/issues/391) | Warn before a word lookup nobody can define — the free pre-flight on the paid path, and the piece #406 would most benefit from |
| [#392](https://github.com/Kuantor/kuantorflow/issues/392) | Flag words no lexicon has in the upload-notes review popup — the same check on a third surface |

### Highest Prio

| Ticket | |
|---|---|
| [ai_agent#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend the meanings of cards |
| [ai_agent#61](https://github.com/Kuantor/ai_agent/issues/61) | Show the time of messages in the agent's chat |
| [#99](https://github.com/Kuantor/kuantorflow/issues/99) | Show a message about API credits if needed |
| [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective and imperfective verb answers as equal |
| [#185](https://github.com/Kuantor/kuantorflow/issues/185) | Images for words and topics |
| [#194](https://github.com/Kuantor/kuantorflow/issues/194) | Copyright-safe image uploads |
| [#216](https://github.com/Kuantor/kuantorflow/issues/216) | Descriptions for topic sections |

### In Progress

| Ticket | |
|---|---|
| [#110](https://github.com/Kuantor/kuantorflow/issues/110) | Learner's vs Collegiate as the EN→EN explanatory dictionary — still the gate for Merriam-Webster's return |

### Human

| Ticket | |
|---|---|
| [#147](https://github.com/Kuantor/kuantorflow/issues/147) | More accurate classification of words in the *General* topic |
| [#236](https://github.com/Kuantor/kuantorflow/issues/236) | Investigate more word activities: ten candidates, researched and costed |

### Todo

| Ticket | |
|---|---|
| [#19](https://github.com/Kuantor/kuantorflow/issues/19) | Add a tutorial tip above the *Look up & save* button |
| [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards between local and remote databases |
| [ai_agent#47](https://github.com/Kuantor/ai_agent/issues/47) | Audit the issue-61 changes: ai_agent code the embedded Mykola does not need |
| [ai_agent#49](https://github.com/Kuantor/ai_agent/issues/49) | Runner setting for the number of last logs |
| [#56](https://github.com/Kuantor/kuantorflow/issues/56) | Privacy disclosure and history bootstrap for per-user chat memory |
| [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme for the website |
| [#75](https://github.com/Kuantor/kuantorflow/issues/75) | About popup: the agent's widget overlaps it |
| [#76](https://github.com/Kuantor/kuantorflow/issues/76) | Scheduled recaps with random log selection |
| [#84](https://github.com/Kuantor/kuantorflow/issues/84) | Darken the main page when the welcome popup is shown |
| [#120](https://github.com/Kuantor/kuantorflow/issues/120) | Settings popup: pin the close button while the dialog scrolls |
| [#140](https://github.com/Kuantor/kuantorflow/issues/140) | Upload notes: support text-layer PDFs |

### Nice To Have

| Ticket | |
|---|---|
| [automation#4](https://github.com/Kuantor/kuantorflow_automation/issues/4) | Automated tests for the Mykola agent |
| [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign the website for a modern look |
| [ai_agent#51](https://github.com/Kuantor/ai_agent/issues/51) | User-preferred nicknames in place of email login |
| [#85](https://github.com/Kuantor/kuantorflow/issues/85) | Full-screen widget mode |
| [#88](https://github.com/Kuantor/kuantorflow/issues/88) | Document how to run backup and restore |
| [#90](https://github.com/Kuantor/kuantorflow/issues/90) | Tell the user that logs are stored on the server? |
| [#92](https://github.com/Kuantor/kuantorflow/issues/92), [#94](https://github.com/Kuantor/kuantorflow/issues/94) | Spaced-repetition review mode — **the same ticket filed twice**, and both specify storage that cannot work on a shared deck; see below |
| [#129](https://github.com/Kuantor/kuantorflow/issues/129) | Smart word-manipulation techniques for exercises and distractors |
| [#136](https://github.com/Kuantor/kuantorflow/issues/136) | Images on flashcards, stored as files |

### Filed earlier, still open and not on the board

| Ticket | |
|---|---|
| [#337](https://github.com/Kuantor/kuantorflow/issues/337) | *Fill the gap*: carry the words you could not remember into the next round |
| [#338](https://github.com/Kuantor/kuantorflow/issues/338) | Long-term recall memory in its own table — **the storage decision #92/#94 need**, and the phase-1 half is small and has no dependencies |
| [#339](https://github.com/Kuantor/kuantorflow/issues/339) | Investigation: what teachers and students actually need in a classroom |
| [#144](https://github.com/Kuantor/kuantorflow/issues/144) | Superseded by #191, which shipped on 28 August. Worth closing as not planned |
| [#210](https://github.com/Kuantor/kuantorflow/issues/210) | Drop the redundant topic string — **blocked on [ai_agent#67](https://github.com/Kuantor/ai_agent/issues/67)**, and the order matters |
| [#227](https://github.com/Kuantor/kuantorflow/issues/227) | Widen the edit-card popup; investigated, and #375 deliberately left it alone |
| [#323](https://github.com/Kuantor/kuantorflow/issues/323) | 13 cards holding a Russian translation in the English explanation — the production repair is done, the issue is still open |

**ai_agent has now had no activity for three editions running**, its most recent change remaining #81 on 17 August. Nothing in these two weeks needed one — #382 worked without an agent change precisely because ai_agent#68 had already moved Mykola's reads into host-injected callables — but ai_agent#67 is the blocker for #210, and #210 is phase three of a migration that is otherwise finished.

---

*Report generated 11 September 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). This edition covers two weeks rather than one. Merged-PR window: 28 August 2026 (kuantorflow#367) through 9 September 2026 (kuantorflow#410, kuantorflow_automation#143); ai_agent had no activity this period, its most recent change remaining #81 on 17 August.*
