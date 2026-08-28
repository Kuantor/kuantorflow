# KuantorFlow — Weekly Development Report

**Period:** 22 – 28 August 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

Last week finished the feature set. This week the app was **hit by an outage it did not cause, and spent the rest of the week being made honest** — about what it can do, what it is showing, and what it has quietly been storing. On 25 August both translation backends stopped working within hours of each other: Google returned 429 and Bing returned 404, and every word lookup on the site failed. That was not bad luck. Both were undocumented endpoints that nobody had licensed to us, which [#199](https://github.com/Kuantor/kuantorflow/issues/199) had put in writing weeks earlier as a risk of exactly this shape.

The response ran in two halves and both are more valuable than the outage was costly. The lookup now **degrades instead of dying** — with no translator answering, a card is still built from the dictionary alone, because an English explanation and examples are most of what a B2–C1 card is worth — and the four scraped providers were replaced by **four licensed ones**, declared in a single registry that the Settings panel, the availability check and the dispatch all read from. The same registry pattern was then applied to the dictionaries, which is how Merriam-Webster stopped being offered on a deployment where it cannot work.

The other half of the week came from a one-line bug report: the add-card popup was not showing a card's example sentences. It had been saving them and displaying them to nobody for months. Making them visible immediately exposed a **second** defect underneath — every scraped sentence carried a space before its full stop, `Are your grandparents still alive ?` — which had been sitting in roughly one card in eleven since the deck was seeded. Both are fixed, and a maintenance script repaired **50 rows in production**.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | Outage response, licensed providers, the review popup, punctuation at the source, artwork, topic ordering | 14 |
| **kuantorflow_automation** | A parallel test PR for every change, plus a production repair script; suite → 1,664 offline | 13 |
| **ai_agent** | No changes; untouched since 17 August | 0 |
| **Total** | | **27** |

**14 issues closed, all 14 as completed — none closed as not planned.** Every one of them was also *filed* inside this window: nothing closed this week had been waiting. That is the signature of a week driven by what the app turned out to be doing wrong rather than by a plan, and it is the second week running with no ticket declined, which is worth watching rather than celebrating — a backlog where nothing is ever rejected is usually one where nothing speculative is being written down.

*All 72 commits across the three repositories were authored by Kuantor (37 in kuantorflow, 35 in kuantorflow_automation, none in ai_agent). **No GitHub Copilot commits were found.** 37 of them carry a `Co-Authored-By: Claude` trailer, which is the assistant's authorship recorded in the commit rather than a second author of the work.*

The regression suite grew from **1,496 to 1,664 passing offline tests**, and to **1,754** with the opt-in database and live-site tests — 168 new offline tests against 14 app changes.

---

## Completed Work by Theme

### 1. Both translators died, and the lookup learnt to survive it

[#348](https://github.com/Kuantor/kuantorflow/issues/348) is the ticket nobody wants to write: on 25 August, looking up any word failed. Google Translate's undocumented endpoint began answering **429** and Bing's answered **404**, within hours of each other, and since a lookup needs a translator to decide which cards to build, the failure was total.

Two changes answer it, and the order matters. [#349](https://github.com/Kuantor/kuantorflow/issues/349) made the lookup **degrade**: when no translator answers, the cards are built from the *dictionary's* parts of speech with the translation fields left empty — a deliberate inversion of the usual rule, since normally the translator decides what cards exist. A card with an English explanation and three examples is most of the value; a card that does not exist is none of it. The same ticket added `fill_missing_fields()`, because otherwise the fix would have created a trap: duplicate prevention refuses to save a word twice, so a card saved during an outage could never be improved by looking the word up again, which is exactly what a learner would try.

Then [#353](https://github.com/Kuantor/kuantorflow/issues/353) replaced the scrapers with **Claude, Microsoft Translator, DeepL and Google Cloud Translation** — all four licensed, all four declared once in a `TRANSLATORS` registry that the Settings panel, the availability check and the dispatch render from. A provider is offered only where its key is configured, so a deployment shows what it can actually do. **One caveat for the demo: only Claude is configured today**, and its key arrives through the `ai_agent` import rather than the app's own environment, which is a fragile route for something the site now depends on.

[#351](https://github.com/Kuantor/kuantorflow/issues/351) is the smaller half of #349 and the one worth remembering: the notice explaining a degraded lookup was rendered *outside* the popup's scrolling region, which pushed the cards past the dialog's bottom edge. The markup was right and the page was wrong — the third instance of that same shape this month.

### 2. The popup was hiding half of every card

The review popup shows a card before it is written. It displayed the word, the explanation and both translations, and kept the example sentences in hidden inputs — saved with the card, shown to nobody, in the one panel whose entire job is looking at a card ([#357](https://github.com/Kuantor/kuantorflow/issues/357)). The user guide had described editing them there since 16 August, and since Mykola answers from that guide, he had been telling learners to do something the interface did not offer.

They are now editable textareas, one example per line, in the same field order as the edit dialog — whose own comment already claimed the two were "the same fields as the review popup". Sizing them turned out to be the real work: three Oxford sentences need 140px at the popup's desktop width and 180px on a phone, so a two-row box would have been most of the way back to hiding them. The boxes now measure themselves against their own content, and three separate bugs in that measurement were found only by looking at the rendered page — a box fighting its own scrollbar into settling exactly one line short, a border-width miscalculation, and a box fitted before the layout settled standing **2,935 pixels** tall.

### 3. Every scraped sentence had a space before its full stop

Showing the examples exposed something underneath them ([#359](https://github.com/Kuantor/kuantorflow/issues/359)): `Are your grandparents still alive ?`. The cause is one line the whole parser depends on. Every extraction reads a node as `get_text(" ", strip=True)`, and that separator is load-bearing — without it Oxford's markup glues words together, `literacy and numeracy.` becoming `literacy andnumeracy.`. Its cost is a space wherever an element ends and punctuation follows, which is precisely what happens when a sentence ends on the word being looked up. It was never sporadic; it was systematic, and a lookup's own examples are the sentences most likely to end on their own headword. All three for *alive* did.

The repair is one normaliser at the single seam where scraped markup becomes text a learner reads. Notably, the notes-import half of the parser had carried a *private* fix for the same separator's other footprint — the padded `( context )` bracket — since 23 July, and the dictionaries never got one. Two spellings of one rule is how this defect survived; there is now one.

### 4. Settings stopped offering what this deployment cannot do

Three tickets, one principle. [#352](https://github.com/Kuantor/kuantorflow/issues/352) took Bing off the panel without deleting the backend. #353 greyed each translator this deployment has no key for, naming the environment variable it needs. And [#365](https://github.com/Kuantor/kuantorflow/issues/365) did the same for **Merriam-Webster**, which had been offered exactly like Oxford while answering 403 to PythonAnywhere's IPs since July — so choosing it on the deployed site produced cards with no English explanation, silently, because a dictionary that returns nothing is not an error anywhere in the lookup.

The rule these share is worth stating plainly: **an unavailable option is greyed and labelled, not hidden.** An absent option reads as "this app cannot do that"; a greyed one reads as "this copy is not set up for it", and only one of those is true. It is the same call [#261](https://github.com/Kuantor/kuantorflow/issues/261) made for an unfinished game tile.

One honesty note carried in the code itself: `MERRIAM_WEBSTER_API_KEY` gates the *choice* and buys nothing yet — the backend still scrapes and sends no key anywhere. It is named now because it is the variable the real thing will need, and [#110](https://github.com/Kuantor/kuantorflow/issues/110), still in progress, is what makes it mean something.

### 5. Four follow-ups to the generated text, and one to the topic list

The generated-text activity shipped last week and immediately produced four tickets, all closed on 23 August. [#340](https://github.com/Kuantor/kuantorflow/issues/340) prints a **gap-fill worksheet** from a generated text — the first thing in the app aimed at a teacher rather than a learner. [#344](https://github.com/Kuantor/kuantorflow/issues/344) fixed texts stopping mid-word: nothing checked the model's `stop_reason` and the token ceiling had no headroom. [#346](https://github.com/Kuantor/kuantorflow/issues/346) made a longer text actually carry more of the learner's words, and corrected a line on the page that claimed something it did not mean. [#342](https://github.com/Kuantor/kuantorflow/issues/342) stopped a round that named no topics from being remembered as though every topic had been chosen.

[#363](https://github.com/Kuantor/kuantorflow/issues/363) added a Settings switch, **on by default**, that orders topics alphabetically inside every section. What it overrides is real — `topics.position` carries the seeded curriculum, running from what a B2 learner meets first towards what they meet last — so it is a switch rather than a change of ordering, and turning it off gives the curriculum back.

### 6. Artwork, a stray file, and a repair run against production

[#361](https://github.com/Kuantor/kuantorflow/issues/361) replaced the background and the gate picture. It also removed `Copilot_20260826_023410+3.jpg`, an artwork export that had ridden into the #353 translator commit on a `git add` of the directory and sat in the repository unreferenced. The test that now catches that — every top-level image in `static/img` must be named by some template, stylesheet or module — is the kind of check nobody writes until it has already happened once.

Finally, the punctuation repair was taken to production. `maintenance/tidy_punctuation.py` tidied **51 of 581 cards locally and 50 in production** on 27 August. That the two numbers are nearly identical is the useful part: the decks were seeded eight days apart and production's is the better copy, so the artifact was never a bad seed run — it was in the scraper the whole time, in both decks, in the same proportion.

---

## Technical Highlights

**One declaration per family.** `TRANSLATORS` and now `DICTIONARIES` join `ACTIVITIES` and `MIGRATIONS` as single declarations that several surfaces render from. Adding a fifth translator is one entry, not one entry and three templates. This is now the repository's most reliable pattern, and #365 took twenty minutes largely because #353 had already made the shape obvious.

**Degrade rather than fail.** #349's dictionary-only card and #365's fallback to Oxford are the same judgement in two places: when part of a feature is unavailable, deliver the part that works and say so. The alternative — an error, or worse, a silently empty result — is what both tickets were filed about.

**Repair and prevention share one function.** The maintenance script imports the app's own `_readable()` rather than reimplementing its rules, and selects rows by asking whether that function would change them. A regex would have been a second description of the artifact, and the two would have disagreed the first time either changed. That is precisely how #359 came to exist.

**A missing key means "leave it alone".** The card editor's rule — only fields *present* in the submitted entry are written — is what let a punctuation repair touch six columns without being able to lose anything in the other twelve. Small decisions made for one feature keep paying for themselves in tools written months later.

---

## Lessons Learned

**1. Making a thing visible is what tests it.** The examples had been stored, saved and never displayed. Within an hour of appearing on screen they exposed a scraping defect present in roughly one card in eleven, in two decks, since the deck was seeded. No test would have found it, because every test asserted what the code produced rather than what a person would read.

**2. A test can pass against broken code, and did — twice.** A regression test written after a fix is written against code that already works, so every assertion is satisfied by construction. This week one test for a display window passed against a deliberately broken implementation, because the window it happened to choose still contained the change; it was rewritten to assert the property that mattered. The practice of breaking the fix and watching the test fail is now recorded in `CLAUDE.md` ([#356](https://github.com/Kuantor/kuantorflow/issues/356)) along with the reason the restore step is dangerous — `git checkout --` discards everything uncommitted in a file, which has silently reverted finished work three times in this repository.

**3. Loud breakage is a feature.** Adding one keyword argument to a widely-stubbed function failed **210 tests at once**. That was the right outcome: the route catches the resulting error and renders an *empty* browse page, so in production the first symptom of a missed caller would have been a page with no topics and no error at all.

**4. Undocumented endpoints are a debt with a due date.** #348 was predicted in #199 and arrived anyway, taking the entire lookup with it for a day. Both replacements and the fallback are better than what was there before — but the week they cost was scheduled by Google and Microsoft, not by us. Two scraped dictionaries remain: Oxford, which the site depends on, and Reverso.

**5. Verification reports did not improve; they stopped.** The previous two editions raised this. Three verification reports shipped in the 15–21 August window against 32 app pull requests; **this week zero shipped against 14**. `CLAUDE.md` exempts small pull requests, and several this week genuinely were — but not the outage response, the licensed-provider registry, or the production data repair. This is the third consecutive edition to report it, and the trend is now downward rather than flat.

**6. The browser remains where layout bugs live.** Three separate sizing defects in the review popup were invisible to a passing test suite and obvious the moment the rendered page was measured. That is now the fourth month in which the same lesson appears in this section, and the practice that catches them — render the real page, measure it, do not trust the markup assertion — is holding.

---

## Plans for Next Week

The plan remains [#331, *Preparation for Production Launch*](https://github.com/Kuantor/kuantorflow/issues/331), which ranks everything still open against two different deadlines. Three of its lines are now out of date and are corrected here: wave two is finished and **no tile on the front page is greyed**, the #323 data repair has been run in production, and #359's punctuation repair has now been run there too.

### Part 1 — before the demo

| Priority | Ticket | Why it matters for this audience |
|---|---|---|
| ⭐ **P0** | [#231](https://github.com/Kuantor/kuantorflow/issues/231) — Oxford misses `can`, `do` | A teacher's first instinct is to look up an ordinary word, and `can` currently returns nothing. This is a bug in the app's core promise, in front of the people most likely to test it. |
| ⭐ **P0** | [#258](https://github.com/Kuantor/kuantorflow/issues/258) — *Real or fake* marks a right answer wrong | The game tells a learner a real English word is not one. Of everything open, this is what an English teacher is most certain to catch and least likely to forgive. |
| **P1** | [#99](https://github.com/Kuantor/kuantorflow/issues/99) — say so when API credits run out | Turns a silent failure into a sentence. With the translator now also depending on the Anthropic key, this covers more than the chat widget did when it was filed. |
| **P1** | [#19](https://github.com/Kuantor/kuantorflow/issues/19) — a tip above *Look up & save* | The teachers will be first-time users, and nothing on the front page says what to do first. |
| **P2** | [#147](https://github.com/Kuantor/kuantorflow/issues/147), [#227](https://github.com/Kuantor/kuantorflow/issues/227) | The messy `General` topic, and the edit popup's cramped fields — the examples boxes now size themselves, so that dialog is visibly the odd one out. |

Four items in #331 are not tickets at all and are done on the morning: check the Anthropic credit balance, hard-refresh after the last deploy ([#300](https://github.com/Kuantor/kuantorflow/issues/300) makes a stale stylesheet render a *plausible wrong page* rather than a broken one), play each game once on the deck being shown, and decide which of the four pitches is being made.

### Part 2 — launch blockers

| Priority | Ticket | Note |
|---|---|---|
| ⭐⭐ **P0** | [#199](https://github.com/Kuantor/kuantorflow/issues/199) — anonymous limits | The one true launch blocker, and the ticket that predicted this week's outage. Until it is done, publishing exposes an unauthenticated endpoint that spends Anthropic tokens and a translation proxy that will get the deployment's IP blocked. |
| ⭐ **P0** | [#300](https://github.com/Kuantor/kuantorflow/issues/300) — versioned static URLs | Demo-optional, launch-critical: after launch, every CSS or JS deploy silently half-breaks the page for returning visitors, and nobody reports it because it still renders. |
| **P0** | [#90](https://github.com/Kuantor/kuantorflow/issues/90) | Tell users their logs are stored on the server. |
| **P1** | [#56](https://github.com/Kuantor/kuantorflow/issues/56), [#88](https://github.com/Kuantor/kuantorflow/issues/88) | Privacy disclosure for per-user chat memory; the backup/restore runbook. |

### Part 3 — after launch, by value

| Ticket | |
|---|---|
| [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign for a modern look — the biggest single lever on "does this look real" |
| [#92](https://github.com/Kuantor/kuantorflow/issues/92) / [#94](https://github.com/Kuantor/kuantorflow/issues/94) | Spaced-repetition review — the feature a teacher is most likely to ask for by name, and the deck already has what it needs |
| [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme |
| [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export / import cards |
| [#191](https://github.com/Kuantor/kuantorflow/issues/191) | *Look up & update* — refill a card from the dictionaries, which #349's `fill_missing_fields()` has now half-built |
| [#178](https://github.com/Kuantor/kuantorflow/issues/178) | Rename a topic, and bulk move |

### The board, by column

| Column | Items |
|---|---|
| **Highest Prio** | ai_agent#19 (Mykola extends card meanings), ai_agent#61 (message times), #100 (perfective/imperfective answers), #185 (images for words and topics), #194 (copyright-safe image uploads), #216 (section descriptions) |
| **In Progress** | [#110](https://github.com/Kuantor/kuantorflow/issues/110) — Learner's vs Collegiate as the EN→EN dictionary, which #365 has now made the gate for Merriam-Webster's return |
| **Todo** | #19 (tutorial tip), #25, #47, ai_agent#49, #56, #74, #75, #76, #84 |
| **Nice To Have** | ai_agent#4, #22, #85, #88, #94, #129 |
| **Human** | #147, [#236](https://github.com/Kuantor/kuantorflow/issues/236) (six unchosen activity candidates, each with a recorded reason) |

### Filed this week, not yet on the board

| Ticket | |
|---|---|
| [#337](https://github.com/Kuantor/kuantorflow/issues/337) | *Fill the gap*: carry the words you could not remember into the next round — session-scoped, and the smaller half of #338 |
| [#338](https://github.com/Kuantor/kuantorflow/issues/338) | Long-term recall memory: what each learner knows, in its own table — the schema decision behind any future review mode |
| [#339](https://github.com/Kuantor/kuantorflow/issues/339) | Investigation: what teachers and students actually need in a classroom — worth answering *before* the demo, since the demo is the cheapest chance to ask |

---

*Report generated 28 August 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). Merged-PR window: 21 August 2026 (kuantorflow#336) through 28 August 2026 (kuantorflow#366, kuantorflow_automation#122); ai_agent had no activity this period, its most recent change remaining #81 on 17 August.*
