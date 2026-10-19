# KuantorFlow — Development Report

**Period:** 17 – 19 October 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**A short period with two threads: the Quiz learned to judge answers the way a teacher would, and the cards' English text got a plan.** In three days, the Quiz began accepting either aspect of a verb, from pairs read in advance from Wiktionary, and having Claude judge an answer close in meaning, shown in green, yellow or red. Rebuild the sentence stopped dealing chips that are not words. The admin emails gained the daily visitor count, the Settings panel stopped showing providers this site cannot use, and the activity logs now keep one event to one line. Alongside the code, an introductory video for learners and teachers, *Meet KuantorFlow*, was checked against the site; that check and a look at how the cards' explanations and examples are sourced and credited produced two umbrellas and fifteen new tickets, with a writing guide for card text already merged.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 9 |
| kuantorflow_automation | 7 |
| ai_agent | 0 |
| **Total** | **16** |

**Nine issues were closed**: eight completed and one declined. [#430](https://github.com/Kuantor/kuantorflow/issues/430) was closed as not planned, because [#459](https://github.com/Kuantor/kuantorflow/issues/459) settled the same Settings question the other way; that ends the conflict between them that the last two editions reported. **Fifteen tickets were filed**, all in kuantorflow, and two of them were also closed within the period.

The test suite grew from **3,042 to 3,163** offline tests, and from 3,245 to **3,376** with the database and live tiers included. Both tiers pass; three tests skip in the full run, as before.

*All 33 commits across the three repositories were authored by Kuantor. The 16 recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address. The other 17 all carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. The Quiz judges answers like a teacher

Both changes apply to the Quiz's English → Ukrainian/Russian direction, and both run before an answer is graded, so a green answer also counts for spaced repetition.

- **[#100](https://github.com/Kuantor/kuantorflow/issues/100): either aspect of a verb is right.** *відшкодувати* now counts for *відшкодовувати*, and *сложить* for *складывать*, and the results say which form the card stores. The pairs are **read in advance** from Wiktionary into a new table, `aspect_pairs`, by `scripts/fill_aspect_pairs.py`, and a new verb card fills its own pairs after it is saved, at most one Wiktionary request per page. So grading only looks a pair up: no wait, no paid call, nothing to fail mid-quiz. On the local deck, 83% of Russian and 56% of Ukrainian verb forms found a partner, matching the ticket's 30 September measurement. Only the *fact* that two forms pair is stored, never Wiktionary's text.
- **[#612](https://github.com/Kuantor/kuantorflow/issues/612): answers close in meaning, in three colours** (Anton's addition). An answer that matches neither a translation nor the other aspect is judged by Claude: 🟩 *accepted*, a correct translation the card doesn't list, counts; 🟨 *close* is shown apart (`Score: 7 / 10, plus 1 close`) and counts as wrong for recall; 🟥 *wrong* comes with the model's short note. Each colour also carries its word, so it reads without colour and in print. The order is the design: the exact match first, then the aspect pairs, then **verdicts already stored**, and only what is left goes to **one call per round**, under its own three daily ceilings. Before choosing the prompt, 31 hand-labelled answers on 12 real cards were run through Haiku: the final prompt agreed with the label on 24 and was **never more generous** than it, which is the direction the ticket cared about.

### 2. Rebuild the sentence deals only words

**[#614](https://github.com/Kuantor/kuantorflow/issues/614)** was found while taking screenshots for the video: a dictionary example carrying a bracketed gloss, *a verdict (= gave a verdict) of guilty*, put chips like `(=` and `verdict)` in the pool, and an alternation, *a film/an art/a music critic*, put `film/an`. The fix in `games.sentence_tokens()` removes the gloss before the sentence is counted, so what is left is still the dictionary's own sentence, and refuses a sentence that still holds a bracket, slash or `=`, the way a sentence of the wrong length already was. Commas stay attached to their word, by design. On the local deck, sentences dealing such a chip went from **42 to 0**; 14 glossed sentences stay playable once cleaned, and one card sits this game out. 45 real rounds in a browser dealt 591 different chips, none of them broken.

### 3. Admin emails and Settings

- **[#600](https://github.com/Kuantor/kuantorflow/issues/600): the daily email counts visitors.** It became a paragraph of #593's daily email rather than a second email: visitors (anonymous and signed in), visits, active minutes, pages, the busiest Kyiv hour, and totals since counting began, robots excluded as #598 recorded them. Anton's own visits sit on a line of their own and are not counted as activity. The guide's privacy section says so.
- **[#459](https://github.com/Kuantor/kuantorflow/issues/459): Settings shows only the providers this site can use.** No greyed rows and no environment-variable names in a panel learners open: on this site that leaves Claude as the translator and two dictionaries. The rest are **hidden, not deleted**, so a key in `.env` brings one back with no code change, and `docs/future-enhancements.md` records how. Saving Settings no longer rewrites a choice the learner can't see: a provider group is sent only when a different radio is ticked, checked in a real browser with a stored provider that this site cannot use.

### 4. One event, one log line

**[#557](https://github.com/Kuantor/kuantorflow/issues/557) and [#558](https://github.com/Kuantor/kuantorflow/issues/558)**, under the observability umbrella [#560](https://github.com/Kuantor/kuantorflow/issues/560). A line break in a logged value used to split one event over two lines, the second without a time or an action, and every reader that counts lines miscounted it. `applog` now escapes all eleven characters that `str.splitlines()` breaks at, and the lookup routes squash whitespace inside a word, so `ice` + a line break + `cream` is looked up as *ice cream*. And `_write()`'s two named parameters are now positional-only, so a field called `action=` or `name=` can no longer raise before the helper's own error handling starts.

### 5. Production's numbers in one file

**[#607](https://github.com/Kuantor/kuantorflow/issues/607)**: `scripts/weekly_numbers.py` gathers what this report needs from PythonAnywhere into one read-only file: AI usage, requests per endpoint with the slowest ten and the paths that answered 404, dropped translations, admin emails, and visits. It names no person; a test checks that there is no `@` anywhere in it. This edition is the first to use it.

### 6. Card text: a plan, a guide and two umbrellas

Checking the video against the site, and looking at where the cards' English explanations and examples come from, led to planning work rather than code:

- **[#632](https://github.com/Kuantor/kuantorflow/issues/632), *Card text: explanations, examples and their sources*.** Licensing and readability in one place, in landing order: how Wiktionary's senses are joined ([#629](https://github.com/Kuantor/kuantorflow/issues/629)), skipping senses that point into Wiktionary's own page ([#630](https://github.com/Kuantor/kuantorflow/issues/630)), keeping fewer and better-chosen senses ([#631](https://github.com/Kuantor/kuantorflow/issues/631)), an "adapted from Wiktionary" credit for edited text, as CC BY-SA asks ([#618](https://github.com/Kuantor/kuantorflow/issues/618)), Wiktionary as the one explanatory dictionary with the deck refilled ([#617](https://github.com/Kuantor/kuantorflow/issues/617)), a staged and reviewed set of explanations and examples for that refill ([#621](https://github.com/Kuantor/kuantorflow/issues/621)), and a user-guide notice of what an AI writes on the site ([#620](https://github.com/Kuantor/kuantorflow/issues/620)), which Anthropic's commercial terms ask for.
- **`docs/dev/card-writing.md`** ([#622](https://github.com/Kuantor/kuantorflow/pull/622), [#623](https://github.com/Kuantor/kuantorflow/pull/623)): how to write a card's explanation and examples, with each rule tied to the game that needs it (*Fill the gap*, *Rebuild the sentence*, *Spell it*) and the curated deck's shape, measured in October as numbers only. A first batch of five cards was written locally and compared with the cards it would replace: it was better on three of five and all 15 of its examples were usable in the games, against 7 of the 14 they replace. It was weaker where a word has a second common sense or a well-known fixed phrase, and one explanation used a different sense from its examples. Those four lessons are now rules.
- **[#628](https://github.com/Kuantor/kuantorflow/issues/628), *Follow-ups from the Meet KuantorFlow video*.** The gap-fill worksheet is missing from the user guide, so neither /help nor Mykola can explain it ([#624](https://github.com/Kuantor/kuantorflow/issues/624)); homework links that deal every student the same words ([#625](https://github.com/Kuantor/kuantorflow/issues/625)); example sentences written by Claude when the dictionary has none ([#626](https://github.com/Kuantor/kuantorflow/issues/626)); and `docs/dev/promises.md`, the video's public promises written down where reviews can check them ([#627](https://github.com/Kuantor/kuantorflow/issues/627)).

---

## Production

The first edition read with `scripts/weekly_numbers.py` (#607), run on PythonAnywhere on 19 October for the window from 17 October. **Most of this window's traffic was our own**, so these numbers say more about the tools than about learners.

- **433 requests**: 420 answered 200, 6 redirected, 5 answered 304 and only **2 answered 404**, the favicon and one mistyped worksheet address, against 33 scanner requests last edition. 375 were anonymous and 58 signed in.
- **Our own measurements dominate.** 198 requests were topic CSV downloads and 103 were topic pages: nearly all of them scripts reading the public deck for #614, #617 and #629–#631, which is why the anonymous share is high. The video's screenshots and its generated letter account for most of the rest of the 17 October traffic.
- **The home page:** median 250 ms, p95 1.1 s. The slowest request of the window was a home page at **5.0 s with 11 database connections**, the most yet, which keeps it at the top of [#554](https://github.com/Kuantor/kuantorflow/issues/554) (reusing connections). A word lookup took 4.1 s and a generated text 4.3 s, as before; *Flashcards* answered in a median of 148 ms.
- **AI usage:** Haiku made 7 calls, six word translations (about 490 input and 46 output tokens each, close to last edition's 460 and 44) and one generated text. Mykola made 4 calls. **#612's close-answer check made no calls yet**: none of the window's quiz rounds left an unmatched answer, or it was not yet deployed when they were played.
- **Visits:** 9 visits over three days, all signed in, 116 active minutes and 31 pages. No anonymous visitor ran the page's check-in, which fits: the measuring scripts load no JavaScript, and headless browsers are counted as robots. **2 accounts in all, none new.**
- **No admin emails were attempted.** No new account and no account near a limit, and the daily report's task is still to be scheduled on the Tasks tab, so the first daily email with the visitor count from #600 is still to come.
- **The alphabet check dropped nothing** (no `TRANSLATE-DROPPED` lines).

---

## Technical Highlights

**Free checks before paid ones.** The Quiz settles an answer by an exact match, then a stored aspect pair, then a stored verdict, and asks Claude only about what is left, once per round. A round whose answers all match has no wait and no cost.

**Read in advance, look up at the moment.** Aspect pairs are fetched by a script and stored; grading never waits on Wiktionary. A failed batch is left out, never stored as "no partner".

**Hide, don't delete.** Providers this site can't use are hidden from learners and kept in the code, so a key brings one back.

**Verbatim text, our own separators.** Wiktionary's senses are copied word for word under CC BY-SA. What the app may choose is which senses to keep and how to join them, never their wording; the card-text tickets keep to that line.

**One event is one line.** A log is only as countable as its weakest value, so every value is escaped where it is written, not where it is read.

---

## Lessons Learned

1. **A real run finds what a specification misses.** #100's fill found Wiktionary naming several aspect partners in one comma-separated value; 50 rows had been stored as one joined "partner". A test that failed first now covers it.

2. **Measure a prompt before choosing it.** #612's 31 hand-labelled answers showed the first prompt and the final one differ by two agreements, and that neither was ever more generous than a teacher would be.

3. **Hiding a field can change what a save sends.** With an unusable provider hidden, every Settings save would have overwritten the stored choice. The fix was to send a group only when it was changed.

4. **Compare new text with what it replaces before scaling it.** Five cards were enough to find a sense mismatch and to turn four general lessons into rules, before any of the deck's 500 cards were touched.

5. **`--limit` truncates silently, again.** The first closed-issues list for this report missed #100, closed within the window, because the query stopped at its limit. Raising it and checking each number found it.

---

## Plans for Next Week

**The card-text work in [#632](https://github.com/Kuantor/kuantorflow/issues/632)**, in its own order: decide #631 (choose senses by meaning, with the length as a cap), then #629, #630 and #618, before #617 and #621 change any card. **The video's follow-ups in [#628](https://github.com/Kuantor/kuantorflow/issues/628)**, starting with the user-guide section on the worksheet (#624), which is small. **[#599](https://github.com/Kuantor/kuantorflow/issues/599)**, the last admin email (how long a signed-in learner's visit lasted), and the daily report's task on the Tasks tab, still to be scheduled. #554 and #563 keep the targets and baselines named in the last edition.

### Highest Prio

[#100](https://github.com/Kuantor/kuantorflow/issues/100), here for five editions, is done.

| | |
|---|---|
| kuantorflow#144 | Fill the missing translation on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella — **complete; should close** |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### Umbrellas

| | |
|---|---|
| kuantorflow#632 | Card text: #617, #618, #620, #621, #629–#631 and #110 — **new** |
| kuantorflow#628 | Follow-ups from the video: #624–#627 — **new** |
| kuantorflow#595 | Admin emails: #600 done; #599 open |
| kuantorflow#560 | Observability: #557 and #558 done; #554, #556, #559 and #563 open |
| kuantorflow#548 | Hardening Mykola's conversations: #545–#547 here, ai_agent#87, #89 and #91–#93 |
| kuantorflow#553 | Development process: #549, #551, #552 and #566's remaining items |
| kuantorflow#538 | Gaps from a teacher's perspective: #533–#537, #539 |

### Not on the board

| | |
|---|---|
| ai_agent#87, #89, #91–#93 | The five open Mykola hardening tickets, for a third edition |
| kuantorflow_automation#173 | The live smoke check still needs a kuantorflow checkout |

Every ticket filed in this period is on the board.

### Board housekeeping

- ***In Progress*** still holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed, for a fifth edition. [#110](https://github.com/Kuantor/kuantorflow/issues/110) is there too and is now part of #632.
- **Finished umbrellas:** [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) should close.
- **Superseded:** [#22](https://github.com/Kuantor/kuantorflow/issues/22) is replaced by #507.
- **Settled:** #430 against #459, closed this period.

---

*Report generated 19 October 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, and from production's numbers read on PythonAnywhere the same day with `scripts/weekly_numbers.py`, following the process recorded in `reports/README.md` (#252). This edition covers three days. Merged-PR window: 17 October 2026 (kuantorflow#608) through 19 October 2026 (kuantorflow#623), excluding the previous edition's own pull request (#609); kuantorflow_automation#232 through #238; no ai_agent pull requests.*
