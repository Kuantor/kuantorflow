# KuantorFlow — Development Report

**Period:** 27 September – 2 October 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**KuantorFlow now remembers what each learner knows.** On 28 September the spaced-repetition plan that the last edition laid out in build order shipped in a single day: an answer log, an SM-2 schedule derived from it, games that deal due words first, and a *Review (N due)* button. The rest of the period prepared the site to be shown to people. It went to the first impression (the welcome popup, a phone-sized header, tap targets and readable hints), to features a teacher would ask about (printing a topic's words, printing a game's results), and to making paid calls happen only when someone asks for them.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 25 |
| kuantorflow_automation | 25 |
| ai_agent | 2 |
| **Total** | **52** |

**Seventeen issues were closed, all of them completed.** None were declined or closed as duplicates. Three more closed inside the window ([#94](https://github.com/Kuantor/kuantorflow/issues/94), [#451](https://github.com/Kuantor/kuantorflow/issues/451) and [#465](https://github.com/Kuantor/kuantorflow/issues/465)), but the previous edition already counted them.

The test suite grew from **2,273 to 2,579** offline tests, and from 2,435 to **2,763** with the database and live tiers included. Both tiers pass.

*All 144 commits across the three repositories were authored by Kuantor. The 52 recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address. 92 carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. Spaced repetition, built in one day

Six tickets under the umbrella [#487](https://github.com/Kuantor/kuantorflow/issues/487) merged on 28 September, each with its own test PR. A separate report, `reports/2026-09-28-spaced-repetition` ([#491](https://github.com/Kuantor/kuantorflow/pull/491)), covers them in full; in brief:

- **[#338](https://github.com/Kuantor/kuantorflow/issues/338): the answer log.** Every answer a signed-in learner gives in a checked game is appended to `recall_answers` and never changed. It is keyed on **the word and part of speech** rather than the card, so duplicate cards count as one word and a deleted card keeps its history. Deleting an account erases it, and the user guide says so.
- **[#479](https://github.com/Kuantor/kuantorflow/issues/479): the SM-2 schedule**, a cache of the log that `scripts/rebuild_schedule.py` can rebuild at any time. SM-2 is extended with four rules of the app's own. One answer per learner-day moves the schedule. The day is Kyiv's, starting at 04:00. A pass before the due date is practice and moves nothing. A self-marked answer counts for less.
- **[#480](https://github.com/Kuantor/kuantorflow/issues/480): the draw prefers what you need.** Seven games now deal words that are due or never answered first. A word that is not due is down-weighted, never excluded.
- **[#92](https://github.com/Kuantor/kuantorflow/issues/92): *Review (N due)*.** A review is an ordinary round of an ordinary game, dealt only from today's due words. A self-rated review screen would produce exactly the evidence the schedule refuses.
- **[#484](https://github.com/Kuantor/kuantorflow/issues/484) and [#337](https://github.com/Kuantor/kuantorflow/issues/337): *Fill the gap*.** Its "I remember it" ticks are recorded as weaker evidence, and the cards left unticked come back in the next round.

Every test file was proved to fail: 66 deliberate breaks, all caught. Two real bugs were found before release. *Listen and type* dropped each word's part of speech, so every verb it scheduled looked never answered. And *Fill the gap* could swap a missed verb for its noun. The design work that made the day possible was done the week before; see the previous edition.

### 2. Pre-demo polish

[#502](https://github.com/Kuantor/kuantorflow/issues/502) came out of a UI/UX review on 30 September and was shipped as **seven separate pull requests, one per item**. Each was checked on a laptop and a phone before the next began. The eighth item was cancelled after a look at the page as it already was.

- **Tap targets.** Answer options are whole 44px rows (item 1), and the header links are 44px boxes (item 2).
- **Readable text.** Hints are readable over the photograph (item 3), and nothing is smaller than 12px (item 7).
- **Mykola's button never covers the page** (item 4).
- **A styled file picker** on *Upload notes* (item 6), the one control that was still in the operating system's own style.
- **The header.** "Welcome to KuantorFlow" stays on a laptop; a phone shows just "KuantorFlow" and drops the *Home* link, so the remaining links fit one row. The links now sit on the wordmark's baseline (item 5 and three follow-ups).

**[#517](https://github.com/Kuantor/kuantorflow/issues/517): the welcome popup** used to appear before its pictures had loaded. Every picture the pages draw is now WebP (only the tab icon and the link-preview image stay JPEG), and the main image comes in phone- and laptop-sized copies. The popup's two pictures went from 778 KB to 134 KB on a phone and 266 KB at full size. The images are fetched only when the popup opens, and the popup waits for them up to 2.5 seconds. `loading="lazy"` turned out not to stop a hidden image being fetched, so the images carry their addresses in `data-src` until they are shown. ai_agent [#86](https://github.com/Kuantor/ai_agent/pull/86) added a WebP of Mykola's poster.

### 3. What a teacher would ask for

- **[#496](https://github.com/Kuantor/kuantorflow/issues/496): a topic's word list**, as a page to print (or save as a PDF) and as a `.csv` file. Both read through the topic page's own read, so an export can never hold a card the page would not show. The file is UTF-8 with a byte-order mark, which Excel needs for Cyrillic. Cells a spreadsheet would run as a formula are defused, because card text is learners' writing. Wiktionary's text keeps its credit in both, which is the condition of copying it.
- **[#521](https://github.com/Kuantor/kuantorflow/issues/521): printing a game's results.** Ctrl+P used to print the header and Mykola's widget over the results. A print stylesheet now prints just the results, headed by the activity, the topics played and the date, with right and wrong legible in black and white. It was checked by printing to PDF with headless Chrome.
- **[#519](https://github.com/Kuantor/kuantorflow/issues/519): *Odd one out* explains every word** on its results page. Where a card has no explanation, it shows the translation in the learner's preferred language instead.
- **[#524](https://github.com/Kuantor/kuantorflow/issues/524): add more words to an existing topic.** It reuses #406's topic builder rather than building a second one. The learner chooses how many words to add and optionally what kind; Claude proposes words the topic does not yet have; the learner approves the list on #406's own screen. One rule decides both the button and the route. Because a card is filed by *name*, the rule also refuses the one case where the words would land in a different topic of the same name.

### 4. Paid calls only when asked

**[#495](https://github.com/Kuantor/kuantorflow/issues/495): Mykola's recap became a button.** The recap runs on Opus over up to 12,000 characters of past chats. It used to fire by itself every time the chat opened, and again on every return after a break; the second path claimed no daily ceiling at all. Now only *Recap our last chats* calls the model. The button hides once pressed, and comes back after a failure or with a new conversation. ai_agent [#85](https://github.com/Kuantor/ai_agent/pull/85) streams the recap, so it is typed out like any answer, and fast thinking shortens it.

### 5. Smaller fixes, each found by use

- **[#499](https://github.com/Kuantor/kuantorflow/issues/499): *Fill the gap* ignored the picker's *Words* box.** It always dealt the *Cards per round* setting instead. The box now decides when the URL carries it, and the setting covers the routes that have no box.
- **[#463](https://github.com/Kuantor/kuantorflow/issues/463) and [#464](https://github.com/Kuantor/kuantorflow/issues/464): Mykola's chat restart.** The slider became a 1–24 number box, and both number boxes in Settings now clamp what is typed. The store falls back to the default rather than clamping, so 25 hours used to save as 2. Verifying the restart in a real page found a bug the endpoint tests could not see. Every page load overwrote the stored "last message" time with null before reading it back, so an anonymous visitor's chat never restarted after one navigation.
- **[#90](https://github.com/Kuantor/kuantorflow/issues/90): privacy.** The user guide's *Privacy: what the site keeps* section is linked from every page's footer and from Mykola's sign-in popup. It states what the logs hold and for how long, and what deleting an account removes. The discussion in [#56](https://github.com/Kuantor/kuantorflow/issues/56) stays open for its second half.
- **A bug in #406's topic builder,** found while timing #524. The fill passed the learner's settings to `lookup_word()` positionally, but its second parameter is `topic`. So a learner who chose Wiktionary got Oxford, and the translator was whichever came first. The tests' stubs had the same positional shape, which is why nothing noticed.

---

## Technical Highlights

**A log, then a cache.** The schedule can be wrong and be repaired, because it is rebuilt from a log that cannot be. On the day it shipped, the live update and a full rebuild agreed on every word.

**Ask only when asked.** The recap was correct and still wrong. A paid call that fires on a page event spends money for somebody who may never read the result. Moving it behind a button removed a cost and an uncapped path in one change.

**One rule for the button and the route.** #524 asks one function whether a learner may add words, and both the template and both routes call it. A button that offers what the route refuses is a door that says no, and two copies of a rule drift.

**Reuse the machine.** #524 is #406 with the title fixed: the same proposal parser, approval screen, spending guards and progress stream. The only new parts are the prompt and the permission rule.

---

## Lessons Learned

1. **A stub with the wrong signature can copy the bug it should catch.** The topic fill called `lookup_word(word, translator, dictionary)`, and the tests stubbed it as `lambda w, t, d`. Both had the same positional mistake, so they agreed with each other. Stubs now take the real signature, and a test fails if the settings arrive in the wrong parameters.

2. **Measure a number before printing it.** The fill page said "about a second each". Eighteen real lookups per dictionary measured 4.1 s with Oxford and 4.5 s with Wiktionary. Nearly all of it is two Claude translations made one after the other, and a one-second pause added to that. The pause had been copied from the 360-word seeding script together with its reason, which did not apply to a twenty-word fill, so it was removed. The page now says about four seconds.

3. **An endpoint test cannot see the widget.** #464's server side was fully tested and correct. The bug was in the order the browser read and saved its own state, and only a real page showed it.

4. **`--limit` truncated this report's own figures.** Listing closed issues with `--limit 150` silently dropped #90 and #92, which are older than the newest 150. A date search (`closed:>=`) found them. This is the trap `reports/README.md` names, and it was hit anyway.

5. **Small PRs, one at a time, with a check between each.** #502's seven items took seven pull requests over two days. Each was looked at on a phone before the next began, and two follow-ups came straight from that looking: the greeting kept on a laptop, and the badge lift halved.

---

## Plans for Next Week

**[#526](https://github.com/Kuantor/kuantorflow/issues/526) first: switch Reverso off entirely.** PythonAnywhere cannot reach it, so a lookup that falls back to Reverso behaves differently locally than in production, and nobody can see the difference. #221 was exactly that. The UI modernisation in [#507](https://github.com/Kuantor/kuantorflow/issues/507) waits until after the demo.

### Highest Prio

Unchanged for a third edition. [#100](https://github.com/Kuantor/kuantorflow/issues/100) was rewritten on 30 September (verb aspect pairs from a stored table), but not started.

| | |
|---|---|
| kuantorflow#100 | Quiz: accept either aspect of a verb (писати / написати), from a stored pair table |
| kuantorflow#144 | Fill missing translations on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella — **complete; should close** |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### After the demo: modernising the UI

| | |
|---|---|
| kuantorflow#507 | The umbrella for the 30 September review |
| kuantorflow#505 | A visual system: type scale, spacing and colour tokens, calmer surfaces |
| kuantorflow#504 | Home page: lead with the next step (Review / Continue) |
| kuantorflow#503 | Games: one question at a time, with instant feedback |
| kuantorflow#506 | Settings: Basics and Advanced, one line of help per setting |

### Worth doing, already specified

| | |
|---|---|
| kuantorflow#493 | *My progress*: a learner's own page, downloadable as a PDF to send to a teacher. The answer log now makes it possible. |
| kuantorflow#497 | Fill imported cards' explanations and examples from the dictionary before saving |
| kuantorflow#478 | The header banner needs a hover and focus state |
| kuantorflow#481 | Check the SM-2 settings against real answers, **not before late October**, when there is a month of data |

### Not on the board

Every ticket filed in the period is on the board. [kuantorflow_automation#173](https://github.com/Kuantor/kuantorflow_automation/issues/173) (the live smoke check still needs a kuantorflow checkout) is still open and still not on it.

### Board housekeeping

- ***In Progress*** still holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed, as the last two editions noted.
- **Finished umbrellas:** [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) should close. #487 now waits only on #481.
- **Superseded:** [#22](https://github.com/Kuantor/kuantorflow/issues/22) (*Redesign Website for a Modern Look*) is replaced by #507.
- **Conflicting:** [#430](https://github.com/Kuantor/kuantorflow/issues/430) and [#459](https://github.com/Kuantor/kuantorflow/issues/459) still contradict each other, and #526 now touches the same question.

---

*Report generated 2 October 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). This edition covers six days. Merged-PR window: 27 September 2026 (kuantorflow#483) through 2 October 2026 (kuantorflow#525), excluding the previous edition's own pull request (#482); kuantorflow_automation#178 and #180 through #203; ai_agent#85 and #86.*
