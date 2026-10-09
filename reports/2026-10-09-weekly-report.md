# KuantorFlow — Development Report

**Period:** 2 – 9 October 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**The site can now be watched, not only used.** Until this period the logs said what happened to a card or a deck, but not how fast a page was or what the AI cost. Two tickets under the new observability umbrella [#560](https://github.com/Kuantor/kuantorflow/issues/560) changed that. Every request now writes one line with its time and database connections, and every paid model call writes one line with its tokens. The period's other two threads were learners and process. On the learner side, *My progress* gives a learner a report to send a teacher, the Quiz now runs both ways round, and Mykola's chat is cheaper and bounded. On the process side, CLAUDE.md was cut to a fifth of its size, and a reusable tool now proves that each new test can fail.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 15 |
| kuantorflow_automation | 13 |
| ai_agent | 2 |
| **Total** | **30** |

**Eleven issues were closed, all of them completed.** None were declined or closed as duplicates. [#524](https://github.com/Kuantor/kuantorflow/issues/524) also closed inside the window, but the previous edition already counted it. **Thirty-nine tickets were filed**: 32 in kuantorflow and 7 in ai_agent, most of them under four new umbrellas.

The test suite grew from **2,579 to 2,779** offline tests, and from 2,763 to **2,967** with the database and live tiers included. Both tiers pass; the three tests skipped in the full run check games that are still stubs, and every game has now landed.

*All 76 commits across the three repositories were authored by Kuantor. The 29 recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address. The other 47 all carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. Observability: how the site is doing

[#560](https://github.com/Kuantor/kuantorflow/issues/560) started from a plain question: is the site slow anywhere, and what does the AI actually cost? The logs could answer neither. Two of its tickets shipped.

- **[#555](https://github.com/Kuantor/kuantorflow/issues/555): one line per request.** `requests.log` now records every request: method, path (without the query string), endpoint, status, milliseconds, and how many database connections the request opened (`db=`). It names the account by its id, never by email, and the user guide's privacy section says so. Each request also gets a short id, which is sent back as an `X-Request-ID` header and added to every other log line that request writes. So a slow page can be joined to the lookup or the save that made it slow. A request that fails with a 500 is still logged, from the teardown hook. The cost is about 150 µs per request, measured on the cheapest page.
- **[#562](https://github.com/Kuantor/kuantorflow/issues/562): one line per paid model call.** Mykola already logged his tokens; the four calls this repo makes itself did not. These are the word lookup's translator, the generated text, the topic builder and the notes splitter. Each now writes a `MODEL-USAGE` line with feature, model, input and output tokens, cache use, stop reason and time. A text cut off at its token limit still writes its line, since it spent the tokens. `scripts/model_usage_report.py` totals these lines with Mykola's per day, feature and model. It turns tokens into dollars only when given a rates file, because prices in code go stale. One real lookup measured about 432 input and 43 output tokens per language, at 1.6–2.1 s each.

**What production showed in its first days.** Both lines were read on PythonAnywhere on 9 October. The sample is small, and it is mostly Anton's own use, so these are first readings, not conclusions.

- **89 requests from 5 to 9 October**: 81 answered 200, 7 answered 304 and 1 answered 404. None failed.
- **The home page is the slowest page and opens the most connections.** It made up 38 of the 89 requests. Its median was 440 ms and its 95th percentile 4.2 s, and it opens 2 database connections at the median and up to 11. The two slowest requests of the period were both the home page, at 4.2 and 4.5 s with 11 connections each. The next eight, at about 1 s, opened 2 or 7. So the connection count alone does not explain the time. Requests with 2 connections took as long as those with 7, and the two slow outliers may be the first request after a reload. *Flashcards* came next, at 470 ms with 4 connections. Everything else answered in well under a second.
- **The AI usage was all Mykola's**: 7 calls, 1,258 output tokens, 4,619 uncached input tokens, 15,635 written to the cache and 22,627 read from it. Most of what Mykola re-reads now comes from the cache. None of the four Haiku calls ran on production after #562 was deployed, so [#563](https://github.com/Kuantor/kuantorflow/issues/563) has no baseline yet. For the same reason the alphabet check has dropped nothing in production yet (no `TRANSLATE-DROPPED` lines).

### 2. Mykola: cheaper turns and a bounded message

- **[ai_agent#88](https://github.com/Kuantor/ai_agent/issues/88): guide excerpts stay out of the stored history.** Each question is sent with excerpts from the user guide, and those excerpts used to be stored in the conversation along with the question. So every later turn re-sent every earlier turn's excerpts. The history now keeps only the question. The hard part was the prompt cache ([ai_agent#71](https://github.com/Kuantor/ai_agent/issues/71)). Its one breakpoint sat on the newest message, which now differs between the turn that answers it and every turn after, so the naive fix would have stopped the history being cached at all. A second breakpoint now marks the end of the stored history, which is byte-identical from turn to turn. Measured over three live calls, the cached read went from 0 to 5,076 to 5,342 tokens. Each turn reads the whole earlier conversation from cache.
- **[#564](https://github.com/Kuantor/kuantorflow/issues/564): one message is at most 2,000 characters.** The only limit was 1 MB on the whole request, so a single paste of up to a million characters reached Opus as **one** message against ceilings that count messages. The cap is checked before either daily quota, so a refused message spends nothing. The chat box counts down from 90% of the limit. [ai_agent#95](https://github.com/Kuantor/ai_agent/pull/95) adds the same cap to the standalone app. The number was checked against production before merging: of 89 real messages, the median was 34 characters and the longest 187.

Anton noticed a chat on the phone that restarted unexpectedly. That became [#545](https://github.com/Kuantor/kuantorflow/issues/545): the break is measured across devices, not per device. A review of Mykola's conversations produced the umbrella [#548](https://github.com/Kuantor/kuantorflow/issues/548), with [#546](https://github.com/Kuantor/kuantorflow/issues/546)–[#547](https://github.com/Kuantor/kuantorflow/issues/547) here and seven tickets in ai_agent ([#87](https://github.com/Kuantor/ai_agent/issues/87)–[#93](https://github.com/Kuantor/ai_agent/issues/93)). One of them, [ai_agent#87](https://github.com/Kuantor/ai_agent/issues/87), was rewritten at Anton's request to **summarise** a long conversation's beginning rather than trim it, so Mykola never forgets how a chat began.

### 3. For learners and their teachers

- **[#493](https://github.com/Kuantor/kuantorflow/issues/493): *My progress*** ([#531](https://github.com/Kuantor/kuantorflow/pull/531), [#532](https://github.com/Kuantor/kuantorflow/pull/532)). A signed-in learner's own report, saved from the browser as a PDF to send a teacher. It shows known, learning, struggling and due-today counts, the last 14 days of activity, and figures by game, by topic and word by word. It is built entirely from the answer log and the schedule, so nothing new is recorded. The student decides who sees it by sending the file, which is why there is no teacher role ([#492](https://github.com/Kuantor/kuantorflow/issues/492) stays open for a class-wide view). A topic filter and a date range narrow the report, for example to one course and one term. The page says plainly that a word's state is always today's.
- **[#529](https://github.com/Kuantor/kuantorflow/issues/529): today's review list stays all day.** *"I had 7 words to review, picked a game, and the list disappeared."* Grading moved each word to its next date at once, so one game emptied the list. Today's list is now the words due when the learner's day began. That is derived by replaying each word's answers from before today, so there is no new table and every device sees the same list. The badge counts down: *Review (7 due)*, *4 of 7 left*, *7 done today*.
- **[#540](https://github.com/Kuantor/kuantorflow/issues/540): the Quiz both ways round** ([#541](https://github.com/Kuantor/kuantorflow/pull/541), [#542](https://github.com/Kuantor/kuantorflow/pull/542)). The Quiz can now show the translation and take the English word, the productive direction no activity asked for. A synonym from the selection counts: *звільнення* is both *resignation* and *dismissal*. The hint reads *noun · starts with "r"*, after Anton found *(noun, r…)* unclear.

### 4. Cleaner data: the alphabet check

**[#544](https://github.com/Kuantor/kuantorflow/issues/544).** Cards had received 语言学, the non-word *языкознавство* as a Ukrainian translation, and a Ukrainian word hiding a Latin *e*. Every translation now passes a check before it reaches a card. A variant with a letter outside Cyrillic, or with the other language's own letters, is dropped and logged as `TRANSLATE-DROPPED`, never repaired. The ticket proposed an allow-list of punctuation, but that would have dropped correct variants (a semicolon, a slash, a stress mark). So the check looks at letters only, and the Ukrainian apostrophe ʼ is let through by name. A report-only script, `scripts/find_bad_translations.py`, was run on production. It found one card, whose Russian field held the English *inn*. Anton fixed it, and the re-run checked 614 cards with nothing to fix.

### 5. How the work is done

The article *Beyond CLAUDE.md* was read for what applies here, and became the umbrella [#553](https://github.com/Kuantor/kuantorflow/issues/553) with four tickets. [#566](https://github.com/Kuantor/kuantorflow/issues/566), from a Google paper on the same subject, joined it. Two shipped:

- **[#550](https://github.com/Kuantor/kuantorflow/issues/550) with #566's first item: CLAUDE.md is a short core.** The file is loaded into every session, and it had grown to **1,319 lines, about 23,000 tokens**. It is now about 250 lines. It opens with a *Verifying your work* block: which tests to run, the database tier that skips silently, and proving that each test fails. The reasoning and history moved verbatim to `docs/dev/design-notes.md` (31 sections) and the deploy runbook to `docs/dev/deploy.md`. Every line was checked to have arrived. A test keeps the core under 300 lines, and checks that every link and every named tool exists.
- **[#569](https://github.com/Kuantor/kuantorflow/issues/569): `tools/prove_fails.py`.** Each PR had been proving its tests with a throwaway script, and the throwaway scripts had bugs of their own. One break removed a check instead of moving it, and one target was lost to a mangled escape. The tool takes a spec of breaks and applies each one. It runs the tests, then restores the file from its saved text, never from git. It refuses a target that is not there exactly once, a break that changes nothing, and tests that fail before anything is broken. It also flags any break that no test catches. Its first real use, on #562, found exactly that: removing an error guard broke nothing, so a test was added.

### 6. Smaller changes

- **[#528](https://github.com/Kuantor/kuantorflow/pull/528): a user-guide catch-up after #524.** It also corrected CLAUDE.md, which gave the per-account ceilings as 150/20/20 when the code says 40/5/10.
- **[#543](https://github.com/Kuantor/kuantorflow/pull/543): who made KuantorFlow, and how to get in touch.** Nothing on the site said so, and the privacy section told learners to *"ask the site's owner"* without saying how. It is a guide section of its own, so Mykola can answer "who made this?".
- **[#572](https://github.com/Kuantor/kuantorflow/issues/572): the deck's *Flip animation* toggle** was grey on a dark pill. A global `label` rule set its colour, overriding the white it should have inherited from the toolbar. It is white again, and the same rule's margin no longer pushes it 3px off the toolbar's centre line.

---

## Technical Highlights

**Ask the system, not your memory of it.** Both observability lines exist so that later decisions can rest on numbers. Their first reading already corrected an assumption: the home page's time does not follow its connection count.

**One id joins the logs.** `requests.log` is useful on its own, but the request id on every other line is what turns six files into one story per request.

**A cache is a byte-identical prefix.** #88 removed content from the history, and was right to. But a cached prefix that changes by one byte is never read again. The fix moved the cache marker to the part that does not change.

**Refuse before spending.** The message cap sits before both quotas, as every free refusal sits before every paid claim. A refused message must cost nothing, not even a count.

**Drop, never repair.** A translation in the wrong alphabet is discarded and logged, not transliterated. A guessed repair would look exactly like a real translation.

**The always-loaded file is a budget.** CLAUDE.md is paid for in every session. Moving the history out kept every word and stopped paying for it on every task.

---

## Lessons Learned

1. **A test written after the code passes by construction.** Nothing in a green run tells "catches the bug" from "cannot fail". prove_fails.py's first run found one assertion guarding nothing, in a PR whose tests all passed.

2. **A break has to be the bug, not a deletion.** #564's first "check after the quotas" break removed the check entirely, and proved something else. Done properly, it fails exactly the two "spends nothing" tests.

3. **Measure a limit before choosing it.** 2,000 characters was checked against production's real messages before merging: the longest was 187. The cap bounds abuse without touching anyone.

4. **A ticket's own proposal can be the bug.** #544's punctuation allow-list would have dropped correct translations. Running the check over the whole deck before implementing showed which rule was right.

5. **Ask what happened before naming a cause.** #545's first explanation (an old tab left open) was wrong. Anton had opened a new tab, and the real cause was that the break is counted across devices. Production's log times are UTC, which caused a second misreading the same day.

6. **Escapes typed into a tool call arrive decoded.** `Ѐ` landed in the source as a raw character, and `\r\n` as real line breaks. Writing the bounds as escapes in the file, or building them with `chr()`, avoids it.

---

## Plans for Next Week

**First, a week of real numbers.** Five days and 89 requests are enough to show where to look, but not to decide. The home page is the place for [#554](https://github.com/Kuantor/kuantorflow/issues/554) (reusing connections), if it is worth doing at all. Timing one connection on PythonAnywhere would settle whether 11 connections can cost seconds. [#563](https://github.com/Kuantor/kuantorflow/issues/563) (Haiku, Sonnet or Opus for the four calls) needs real lookups and generated texts in the usage log before it has a baseline. After that come the rest of the observability umbrella ([#556](https://github.com/Kuantor/kuantorflow/issues/556)–[#559](https://github.com/Kuantor/kuantorflow/issues/559)) and Mykola's hardening ([#548](https://github.com/Kuantor/kuantorflow/issues/548)). [#526](https://github.com/Kuantor/kuantorflow/issues/526) (Reverso off), which the last edition put first, was not started.

### Highest Prio

Unchanged for a fourth edition.

| | |
|---|---|
| kuantorflow#100 | Quiz: accept either aspect of a verb (писати / написати), from a stored pair table |
| kuantorflow#144 | Fill the missing translation on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella — **complete; should close** |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### New umbrellas from this period

| | |
|---|---|
| kuantorflow#560 | Observability: #554, #556–#559, #563 open; #555 and #562 done |
| kuantorflow#548 | Hardening Mykola's conversations: #545–#547 here, ai_agent#87 and #89–#93 |
| kuantorflow#553 | Development process: #549 (REVIEW.md), #551 (plan first, proof table), #552 (a verifier agent), #566's remaining items |
| kuantorflow#538 | Gaps from a teacher's perspective: class-sized limits (#533), unlisted topics (#534), sign-in without Google (#535), phrases (#536), speaking (#537), *Particles* (#539) |

### After the demo: modernising the UI

[#507](https://github.com/Kuantor/kuantorflow/issues/507) and its four tickets ([#503](https://github.com/Kuantor/kuantorflow/issues/503)–[#506](https://github.com/Kuantor/kuantorflow/issues/506)) are unchanged.

### Not on the board

| | |
|---|---|
| ai_agent#87, #89–#93 | The six open Mykola hardening tickets: summarise long chats, card text as data, no empty replies, retrieval in Ukrainian and Russian, the reply language, an evaluation suite |
| kuantorflow_automation#173 | The live smoke check still needs a kuantorflow checkout |

### Board housekeeping

- ***In Progress*** still holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed, for a third edition.
- **Finished umbrellas:** [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) should close. #487 waits only on [#481](https://github.com/Kuantor/kuantorflow/issues/481), not before late October.
- **Superseded:** [#22](https://github.com/Kuantor/kuantorflow/issues/22) is replaced by #507.
- **Conflicting:** [#430](https://github.com/Kuantor/kuantorflow/issues/430) and [#459](https://github.com/Kuantor/kuantorflow/issues/459) still contradict each other, and #526 touches the same question.

---

*Report generated 9 October 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). This edition covers eight days. Merged-PR window: 2 October 2026 (kuantorflow#528) through 7 October 2026 (kuantorflow#573), excluding the previous edition's own pull request (#527); kuantorflow_automation#204 through #216; ai_agent#94 and #95.*
