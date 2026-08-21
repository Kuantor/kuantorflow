# KuantorFlow — Weekly Development Report

**Period:** 15 – 21 August 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This period **finished the app's feature set and turned the question from "what is left to build" into "what is left before people see it"**. Six weeks of word-game tickets closed: the deck now has **ten playable activities and not one greyed tile**, both game umbrellas have every sub-task done, and the app gained its first two abilities that are not testing — it *speaks* a word aloud, and it *writes* a passage of English out of the learner's own vocabulary. **63 pull requests merged** and the regression suite grew from **936 to 1,496 passing offline tests**, plus the opt-in database and live tests, which take the full run to 1,584.

The more important output of the week is not a feature. With the build essentially complete, the backlog was ranked in one place — [#331, *Preparation for Production Launch*](https://github.com/Kuantor/kuantorflow/issues/331) — around a distinction that had been blurred until now: **showing the site to English teachers and opening it to the public are different deadlines with different blockers.** A demo keeps the keyword gate and needs only that nothing is embarrassing in front of people who teach English for a living; a launch needs anonymous limits before an unauthenticated endpoint is allowed to spend money. That ticket, not the project board, is now the plan, and the *Plans for Next Week* section below is drawn from it.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | Five more games, the generated-text activity, the app's voice, the guided save panels, and Mykola reading the user guide | 32 |
| **kuantorflow_automation** | A parallel test PR for every change, two production repair scripts, one manual test plan; suite → 1,496 offline | 30 |
| **ai_agent** | One change: the host may now hand Mykola documents to index | 1 |
| **Total** | | **63** |

**31 issues closed, all 31 as completed — none closed as not planned.** That is unusual and worth naming: every ticket touched this week was specified well enough to be finished as written, which is the dividend of the specify-first weeks that preceded it. Among them are the last four of wave two (#269–#272), the two wave-one games that had been open since July (#130/#131 and #235), and #237, the generated-text activity that had been the largest unbuilt item in the backlog.

*Per the standing request, commit authorship was checked across all three repositories: all **161 commits** were authored by Kuantor, with Claude Opus 5 as co-author on 93 of them. **No GitHub Copilot commits** were found in this range, and no `Co-Authored-By` line names any author other than Claude Opus 5.*

---

## Completed Work by Theme

### 1. Ten activities, and no greyed tile left

The front page has carried greyed-out game tiles since the chassis landed on 11 August. It carries none now. Five games finished this week, and the two umbrella tickets — #233 (wave one) and #265 (wave two) — have every sub-task closed.

- **Fill the gap** (#235, PR #312) — a word cut out of one of its own example sentences. The eligibility rule *is* the feature: a card with no example it can be cut from is not offered, because a sentence shown ungapped hands over the answer. **Every** occurrence goes, not only the first, since one stored example really can hold two sentences using the word. #334 (PR #335) then added optional first and last letter hints and let the length of the blank imply the length of the word.
- **Multiple choice** (#130 and #131, PRs #317/#318, then #319 as PR #320) — a translation and four English words. #130 and #131 were filed as two mechanics and turned out to be one game: #131 has no page, no route and no tile, only a source of plausible wrong answers.
- **Odd one out** (#269, PR #328), **Spell it** (#270, PR #332), **Rebuild the sentence** (#271, PR #333) and **Listen and type** (#272, PR #326) — the four wave-two games, all four shipped on 20 August on the groundwork that preceded them.
- **The groundwork they shared** (#266, PR #324 and #267, PR #325) — card-level eligibility where the rule returns *what it made* rather than a yes, so a round and the value it needs come from one call; and one typed-answer path that normalises once and grades the questions that were actually asked.
- **The picker remembers the length it was played with** (#330, PR #329) — no game had ever written the round length back, so the box could be changed on the picker and never from a game.

**Rebuild the sentence is tap-to-place, not drag-and-drop**, and that was a decision rather than an omission: tap is one handler that behaves identically with a mouse, a finger and a keyboard, where drag needs pointer events, a touch fallback and a keyboard path, and is the least testable thing this suite could be asked to cover.

### 2. The first activity that writes English instead of testing it

**#237** (PR #306) generates a passage of prose from the learner's own words, with those words in bold and a list underneath of which ones actually appeared. **#315** (PR #316) gave it a title. It is the only paid call this repository makes on its own, and every decision in it is about spending as little as possible: the prompt carries the bare words and nothing else — no explanations, examples or translations, which are most of a card's bulk; `max_tokens` is words × 1.5, because 1:1 stops a 150-word request mid-sentence; the result is held for the session so re-reading, flipping back and a stray refresh cost nothing; and regeneration is an explicit button. A 150-word passage costs about an eighth of a cent.

**The highlighting is verified, not requested.** The model is asked for plain prose and the app finds the words afterwards, so the page can report what actually happened — the words that appeared and the ones that did not — instead of trusting a model to mark its own homework. And **the guards run before the call, never after**: a blocked account, then the anonymous nudge, then the per-account and site-wide daily ceilings, and only then Anthropic.

### 3. The app found its voice

**#283** (PR #284) put a pronounce button on the card, **#301** (PR #302) put one in the review popup so a word can be heard before deciding to keep it, and **#268** (PR #327) added a speech-rate setting — because a learner hearing a word for the first time and one revising it want different speeds. All three run through one shared browser speech helper, which is also what **Listen and type** is built on. The manual half of that path — what a real browser does with voices, which cannot be asserted in pytest — was written up as a test plan in its own right (automation #104).

### 4. Saving a card stopped being blind

A cluster of eight fixes on 16 August, small individually and one change together: the learner can now see what they are doing when a card is saved.

- **Choose a topic instead of typing it** (#292, PR #293) and **say which topic reviewed cards are going to** (#294, PR #295) — the typed topic box invited a typo that silently created a new topic.
- **Retire the typed topic box from Browse flashcards** (#290, PR #291) and **fold a section away** (#288, PR #289) — browsing a deck of 500 cards is now navigation rather than scrolling.
- **A stray click no longer throws the cards away** (#296, PR #297) — clicking outside the review popup discarded a whole parsed upload.
- **Keep the question when the page changes under it** (#304, PR #305), and **equal space under the fields in both save panels** (#298, PR #299), where the fix was three CSS lines and the test pins exactly those three.

### 5. Mykola read the manual, and stopped confirming saves he never made

**#286** (PR #287) wrote a user guide for the learner, and **#310** (PR #311, with ai_agent #81) then handed that same guide to Mykola, who indexes it beside his own knowledge. The guide is now the **single source**: the agent repository deliberately describes this app nowhere, because the copy it used to keep drifted until it was answering "why can't I add cards?" from a description written before the permission rules changed. The consequence for the future is a small rule with a long reach — **a feature change a learner would notice is now a guide change**.

**#308** (PR #309) fixed the worst failure shape a study companion has: Mykola confirming a card he had not saved. A save skipped as a duplicate used to return quietly, so he cheerfully reported a card as added, into a topic it was not in. A refusing saver now *raises*. ai_agent #56 (PR #307) gave his avatar the height his caption already took.

### 6. Two data repairs, and the report's own machinery

- **#314** (automation #94) — cards saved before the example-sentence feature had no `examples_en` and nothing in the app could give them one. The evidence was a six-minute margin: the seed ran until 21:44 on 8 August and the feature was committed at 21:38, so exactly one card of 509 — the last of the run — had examples.
- **#323** (automation #99) — 13 local cards held a Russian translation where an English explanation belongs, `smuggle` explained as *контрабанда*. Repaired locally on 19 August and **on production on 21 August**, where there were seven rows rather than thirteen and different ids. The two databases have diverged and both are now clean.
- **#282** (PR #285) — the PDF renderer's own safety check refuses a report that is secretly the browser's error page, and it had been refusing reports that merely *quoted* that error string in their prose. The 15 August edition was rejected twice for describing the trap it exists to prevent. The check is now scoped to the shape of an actual error page, and there is still no flag to skip it. **#281** stopped weekly reports shipping a DOCX (PR #281), leaving Markdown and its PDF render.

---

## Technical Highlights

**Build the shared thing once, on purpose.** The word matcher that finds a card's headword inside a piece of English now has three callers — the generated text, Fill the gap, and Spell it. Two implementations would have disagreed within a month and failed in opposite directions: one showing a sentence that contains its own answer, the other reporting a word as unused while it is on the screen. The same argument produced the single typed-answer path (#267) and the shared speech helper (#268).

**A knob is not its outcome.** The multiple-choice distractor design was rebuilt four times in one day, and every version was justified by reasoning that sounded right. The lever turned out to be *how often* anything is misspelled rather than *which* word gets misspelled — and the setting that controls it ran about six points below the rate it actually produced. Only a sweep showed that.

**Simulate the learner who knows nothing.** What settled the distractor question in one run each time was a naive guesser: a player who knows no English and exploits only the surface pattern. Its score moved 33.3% → 40.0% → 32.3% across the versions, and told the truth where three rounds of careful reasoning had not.

**One rule can be wrong in four places at once.** Duplicate cards mean 131 of the deck's words are held by more than one card, so a round could ask the same word twice. That shipped in four games simultaneously, and was fixed once, in the shared layer — which is the argument for the shared layer.

**A session cookie is a 4 KB design constraint.** Flask signs the session and Werkzeug silently drops it above roughly 4 KB, so what may be held there is a real limit rather than a guideline. The generated text fits because the model is *allowed* to return at most 600 tokens; the measured worst case is 3.2 KB. Anything larger needs somewhere else to live.

---

## Lessons Learned

1. **A regression test written straight after the fix is satisfied by construction.** Six of seven round-level tests for #334 passed with the fix disabled. The fixture's shared word was three letters, which two of the games reject as too short, so those rounds never had the chance to repeat it — and the assertion searched the page for a word most of these games never render plainly. The practice now is to break the fix, watch the test fail, and restore. It costs one command.

2. **This suite asserts markup, and the browser decides things markup cannot show.** Four bugs reached a merged branch with everything green and were found by playing the app: a script read before a deferred file had loaded, a form reset that restored page-load defaults rather than saved ones, a round length nothing wrote back, and a click handler bound to the page's *first* form — which is the settings popup's, rendered far above the content. A probe must reproduce the page, not just the script.

3. **Production and local are not the same database.** The stale-explanation repair moved 13 rows locally and 7 in production, with no overlap in ids. Verifying a repair in one place says nothing about the other, and a script that reports `nothing to repair` is the only acceptable evidence.

4. **An eligibility rule is a feature, not a guard.** Fill the gap cannot use a card whose examples do not contain the word; Spell it cannot use one with no explanation; Scrambled cannot use a word it is unable to disturb. Treating these as error handling produces rounds that hand over the answer. Treating them as the rule — a card that cannot make a question is simply not asked — is what makes the games trustworthy.

5. **Verification reports did not keep pace.** Three were written this period against a week of 32 pull requests in the app repository. The standing convention exempts small PRs, and many of these genuinely were small; but the generated-text activity, the multiple-choice game and the wave-two groundwork were not, and only the first has a report. This was flagged in the last edition too, which makes it a process debt rather than an off week.

---

## Plans for Next Week

**The plan is [#331](https://github.com/Kuantor/kuantorflow/issues/331), not the project board.** The board is materially out of step — it lists the multiple-choice game under *Done* but has never held the wave-two tickets, the games umbrellas or the launch ticket itself. #331 ranks everything still open against two deadlines, and its priorities are an assessment offered for editing rather than a decision already taken. It is reproduced in outline here; the board columns follow it, for continuity with previous editions.

### Part 1 — before the demo

Four of these are not tickets. The demo keeps the keyword gate, so almost nothing in the launch section below is needed for it; what matters is that nothing is embarrassing in front of people who teach English for a living.

| Action | Why |
|---|---|
| **Check the Anthropic credit balance** | If it runs out mid-demo, Mykola stops answering with no explanation. The single most likely way the demo goes wrong, and it costs one look. |
| ~~Run the #323 repair on production~~ | **Already done, 21 August** — 7 rows moved, both databases clean. |
| **Hard-refresh after the last deploy** | [#300](https://github.com/Kuantor/kuantorflow/issues/300): a cached stylesheet renders a *plausible wrong* page rather than a broken one. You would not notice; you wrote the CSS. |
| **Play each game once, on the deck you will show** | Cheaper than any test that can be written, and it is how the last four real bugs were found. |
| **Decide what is being demonstrated** | The deck, the lookup, the games and Mykola are four different pitches. |

| Priority | Issue | Why it matters *for this audience* |
|---|---|---|
| **P0** | [#231](https://github.com/Kuantor/kuantorflow/issues/231) — Oxford lookups miss words whose entries are numbered (`can`, `do`) | A teacher's first instinct is to look up an ordinary word, and `can` currently returns nothing. This is a bug in the app's core promise. |
| **P0** | [#258](https://github.com/Kuantor/kuantorflow/issues/258) — Real or fake sometimes invents a real word | The game marks a learner **wrong for being right**. Of everything open, the one an English teacher is most certain to catch. |
| **P1** | [#99](https://github.com/Kuantor/kuantorflow/issues/99) — say so when API credits run out | Turns a silent failure into a sentence. |
| **P1** | [#19](https://github.com/Kuantor/kuantorflow/issues/19) — a tip above *Look up & save* | These are first-time users, and nothing on the front page says what to do first. |
| **P2** | [#147](https://github.com/Kuantor/kuantorflow/issues/147) — classify the `General` topic | It holds the earliest, messiest cards, including misspelled headwords. Hiding it for the demo is a cheaper answer. |
| **P2** | [#208](https://github.com/Kuantor/kuantorflow/issues/208), [#84](https://github.com/Kuantor/kuantorflow/issues/84), [#75](https://github.com/Kuantor/kuantorflow/issues/75), [#120](https://github.com/Kuantor/kuantorflow/issues/120), [#227](https://github.com/Kuantor/kuantorflow/issues/227) | Small popup snags. Individually trivial; together they are the difference between "a project" and "a product". |

### Part 2 — launch blockers

None of this is needed for the demo. All of it is needed before the gate comes off.

| Priority | Issue | Note |
|---|---|---|
| **P0** | [#199](https://github.com/Kuantor/kuantorflow/issues/199) — what has to be limited before the keyword gate comes off | **The one true launch blocker.** Until it is done, publishing exposes an unauthenticated endpoint that spends Anthropic tokens, a free translation proxy that will get the PythonAnywhere IP blocked, and a shared daily Mykola budget one person can burn in a minute. |
| **P0** | [#300](https://github.com/Kuantor/kuantorflow/issues/300) — versioned static URLs | Demo-optional, launch-critical. After launch every CSS or JS deploy silently half-breaks the page for returning visitors, and nobody reports it, because it still renders. |
| **P0** | [#90](https://github.com/Kuantor/kuantorflow/issues/90) — tell users their logs are stored on the server | Real users, real data, a disclosure to make before rather than after. |
| **P1** | [#56](https://github.com/Kuantor/kuantorflow/issues/56) — privacy disclosure for per-user chat memory | Same class as #90, and needs deciding rather than only writing. |
| **P1** | [#88](https://github.com/Kuantor/kuantorflow/issues/88) — document backup and restore | The scripts exist; the runbook does not. |

### Part 3 — after launch, by value

| Value | Issues |
|---|---|
| **High** | [#92](https://github.com/Kuantor/kuantorflow/issues/92) / [#94](https://github.com/Kuantor/kuantorflow/issues/94) spaced repetition — **the feature a teacher is most likely to ask for by name**, and the deck already has everything it needs · [#22](https://github.com/Kuantor/kuantorflow/issues/22) redesign · [#74](https://github.com/Kuantor/kuantorflow/issues/74) dark theme · [#25](https://github.com/Kuantor/kuantorflow/issues/25) export/import · [#191](https://github.com/Kuantor/kuantorflow/issues/191) look up & update · [#178](https://github.com/Kuantor/kuantorflow/issues/178) rename a topic |
| **Medium** | [#321](https://github.com/Kuantor/kuantorflow/issues/321) and [#322](https://github.com/Kuantor/kuantorflow/issues/322) multiple choice, other directions · [#303](https://github.com/Kuantor/kuantorflow/issues/303) hear Mykola's answers · [#100](https://github.com/Kuantor/kuantorflow/issues/100) perfective/imperfective answers · [#144](https://github.com/Kuantor/kuantorflow/issues/144) translations after MHT parsing · [#216](https://github.com/Kuantor/kuantorflow/issues/216) topic-section descriptions · [#260](https://github.com/Kuantor/kuantorflow/issues/260) a table of generated distractors · [#85](https://github.com/Kuantor/kuantorflow/issues/85) full-screen widget · [#76](https://github.com/Kuantor/kuantorflow/issues/76) scheduled recaps |
| **Low** | [#136](https://github.com/Kuantor/kuantorflow/issues/136), [#185](https://github.com/Kuantor/kuantorflow/issues/185), [#194](https://github.com/Kuantor/kuantorflow/issues/194) images and the copyright question they share · [#140](https://github.com/Kuantor/kuantorflow/issues/140) PDF uploads · [#210](https://github.com/Kuantor/kuantorflow/issues/210) drop `flashcards.topic` · [#110](https://github.com/Kuantor/kuantorflow/issues/110) Learner's vs Collegiate dictionary |
| **Not launch work** | [#129](https://github.com/Kuantor/kuantorflow/issues/129) word-manipulation ideas · [#236](https://github.com/Kuantor/kuantorflow/issues/236) ten researched activity candidates. Listed so nothing is lost, not because anything is expected of them. |

**The other two repositories.** In `ai_agent`, only [#67](https://github.com/Kuantor/ai_agent/issues/67) is coupled to this repository's work — and it is order-critical: it must be **deployed**, not merely merged, before kuantorflow #210 may drop the topic string, or Mykola begins reporting every card's part of speech as its topic, confidently and with no log line. The rest (#61, #51, #47, #19) is Mykola's own backlog. In `kuantorflow_automation`, [#61](https://github.com/Kuantor/kuantorflow_automation/issues/61) brings the test catalogue back in step with a suite that has grown by 560 tests in a week.

### The board, by column

Retained for continuity. It understates what is ready to start, which is why #331 exists.

| Column | Issues |
|---|---|
| **Highest Prio** | kuantorflow #216, #194, #185, #100, #99 · ai_agent #61, #19 |
| **In Progress** | kuantorflow #110 |
| **Human** | kuantorflow #236, #147 |
| **Nice To Have** | kuantorflow #136, #129, #94, #92, #90, #88, #85, #22 · ai_agent #51 · automation #4 |
| **Todo** | kuantorflow #140, #120, #84, #76, #75, #74, #56, #25, #19 · ai_agent #49, #47 |

### Filed this week, not yet on the board

| Issue | Title |
|---|---|
| [#331](https://github.com/Kuantor/kuantorflow/issues/331) | **Preparation for Production Launch** — everything open, ranked against two deadlines |
| [#323](https://github.com/Kuantor/kuantorflow/issues/323) | 13 cards hold a Russian translation in `explanation_en` — **repaired in both databases**, open only for closing |
| [#322](https://github.com/Kuantor/kuantorflow/issues/322) | Multiple choice: show the English explanation instead of a translation |
| [#321](https://github.com/Kuantor/kuantorflow/issues/321) | Multiple choice: the other direction — an English word, four translations |
| [#303](https://github.com/Kuantor/kuantorflow/issues/303) | Hear Mykola's answers, in the language he wrote them |
| [#300](https://github.com/Kuantor/kuantorflow/issues/300) | A cached stylesheet renders a plausible wrong page |

**If there is time for two things before the demo: #231 and #258.** Both are wrong-answer bugs in front of people whose job is knowing the right answer, and both are cheap next to a redesign. **If there is time for one thing before launch: #199.** Everything else can ship late; that one cannot ship after.

---

*Report generated 21 August 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). Merged-PR window: 15 August 2026 (kuantorflow#280) through 21 August 2026 (kuantorflow#335, kuantorflow_automation#109); ai_agent contributed a single PR, #81, on 17 August.*
