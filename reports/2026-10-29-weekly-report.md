# KuantorFlow — Development Report

**Period:** 19 – 29 October 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**The period that finished the move to a topics table, and the last before a pause in active development.** The `flashcards.topic` string that #207 kept beside `topic_id` for the transition is gone from production: Mykola's standalone reader went first, a read-only check proved that no card would lose its topic, and `apply_schema.py` gained its first step that removes something rather than adding it. Around that, the Quiz's new answer judging was deployed and polished after its first real use, two refusals that offered a sign-in to people who could not use one were corrected, and a test-suite hang that had cost two long runs was fixed at its root. An architecture review in the form of interview questions produced three tickets on request safety, which head the list of what should not wait for the pause to end.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 8 |
| kuantorflow_automation | 9 |
| ai_agent | 1 |
| **Total** | **18** |

**Eight issues were closed, all completed**: seven in kuantorflow and [ai_agent#67](https://github.com/Kuantor/ai_agent/issues/67). **Eleven tickets were filed**, all in kuantorflow, and six of them were also closed within the period.

The test suite grew from **3,163 to 3,278** offline tests, and from 3,376 to **3,497** with the database and live tiers included. Both tiers pass; three tests skip in the full run, as before.

*All 36 commits across the three repositories were authored by Kuantor. The 18 recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address. The other 18 all carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. The topics move is finished

In August, #207 gave topics their own table and kept the old `flashcards.topic` string beside the new `topic_id`, because two readers still used it. This period removed both readers and then the string, in the only safe order.

- **[ai_agent#67](https://github.com/Kuantor/ai_agent/issues/67) ([ai_agent#97](https://github.com/Kuantor/ai_agent/pull/97)): Mykola's standalone card store reads topics through `topic_id`.** The website's Mykola already used KuantorFlow's own readers (ai_agent#68), so the risk lived in the standalone app pointed at the same database. Measured on a copy of the real local database with the column dropped, the old code would have reported only 6 "topics" — *noun*, *verb* and the like — with **605 of 655 cards filed under their part of speech** and no error anywhere. The new code gives the same 24 topics with or without the column. The part of speech is no longer a candidate for a topic at all.
- **[#651](https://github.com/Kuantor/kuantorflow/pull/651): a read-only check before the drop.** `scripts/check_topic_column.py` compares each card's string with the name its `topic_id` points at and exits non-zero only if a card would lose its topic. Local: 655 of 655 matched; PythonAnywhere: **620 of 620**.
- **[#210](https://github.com/Kuantor/kuantorflow/issues/210) ([#652](https://github.com/Kuantor/kuantorflow/pull/652)): the string is dropped.** `apply_schema.py` gained `Removed(target, guard)`, a step that counts as done once its object is **gone** — the usual "does it exist?" test would skip a drop exactly when there is work to do — and that refuses to run while any card's string is its only topic. #207's backfill now `requires` the string, so it reads as finished after the drop instead of failing on every later run and on every fresh database. A grep of all three repositories, rather than the ticket's list, found the readers and writers to change, including three maintenance scripts the ticket did not name. **Deployed on 27 October** with a backup first and the web app reloaded *before* the schema step, the one order in which no request writes to a column that is about to go; the live site was then checked by hand.

### 2. The Quiz after its first real use

The Quiz's answer judging (#612) and verb aspects (#100) went live on PythonAnywhere this period, and `scripts/fill_aspect_pairs.py` filled production's pairs: **236 Ukrainian forms, 131 with a partner; 238 Russian, 195 with one**. Using them found two things:

- **[#634](https://github.com/Kuantor/kuantorflow/issues/634): a double full stop** after a close answer's note ("…in someone.. The card says"). The note's own final stop is now dropped where the page reads it, so the notes already stored were fixed too, without a data step.
- **[#636](https://github.com/Kuantor/kuantorflow/issues/636): an exact answer also shows the card's other translations** — *дорогий* for *precious* adds "Also right: дорогоцінний, безцінний". It was the one kind of right answer whose panel said nothing more, and about **80% of the public deck's cards** have other translations to show. It costs nothing: the translations are already on the card.

### 3. Refusals, defaults and the guide say what the code does

- **[#643](https://github.com/Kuantor/kuantorflow/issues/643): a refusal offers sign-in only where signing in helps.** When the whole site's lookup allowance ran out, signed-in learners were told to "sign in", with a button; when the shared anonymous allowance for generated texts ran out, the sign-in that would have helped was not offered. Both now follow the chat's three-way rule, and each `LIMIT` line records the limit that was actually reached (it logged 150 instead of 250, and 60 instead of 20).
- **[#645](https://github.com/Kuantor/kuantorflow/issues/645): `.env.example` states the code's numbers.** It listed five of the twenty settings the code reads, two of them wrong — an anonymous chat pool of 200 that would have been larger than the whole site's 40 — and claimed signed-in learners are never limited. A test now derives the list from `web.py`. Production's `.env` has no limit lines, so the stale values never reached it.
- **[#640](https://github.com/Kuantor/kuantorflow/issues/640): the guide no longer says due words are "dealt first".** The draw makes a known word about five times less likely, never excluded, and shuffles what it picks. Mykola answers from the guide, so he now says the same; the ratio in the guide is checked against the weights in `recall.py`.
- **[#638](https://github.com/Kuantor/kuantorflow/issues/638): the topic builder's headings are white on the photo**, like every other page, found while recording the video. A test now holds every page built on the main layout to the same rule.

### 4. One failing test can no longer hang the suite

**[kuantorflow_automation#247](https://github.com/Kuantor/kuantorflow_automation/pull/247).** The real-MySQL tests' helpers closed a connection only after every statement succeeded. A failed statement kept its table lock alive through the saved traceback, and the fixture's `DROP DATABASE` then waited for it with MySQL's default timeout of a year: twice during #210 the whole tier hung for twenty minutes without a word. All 44 such connections in 21 files now close in `finally`; one test derives the rule from the files, and another reproduces the hang against real MySQL with a five-second lock wait, so a regression fails quickly instead of stalling.

### 5. Tickets filed for after the pause

- **Request safety, from an architecture review:** a CSRF token on every request that changes data, and `/logout` moved off `GET` ([#647](https://github.com/Kuantor/kuantorflow/issues/647)); error handlers that answer JSON requests in JSON instead of Flask's HTML page ([#648](https://github.com/Kuantor/kuantorflow/issues/648)); each JSON route's input validated in one declared place ([#649](https://github.com/Kuantor/kuantorflow/issues/649)).
- **[#650](https://github.com/Kuantor/kuantorflow/issues/650):** drop the three daily counter tables that `action_usage` replaced, reusing #210's removal step. A database-level duplicate guard was considered and **not filed**: a `UNIQUE` key would break the deliberate "Add it anyway", and the race it would close has no recorded occurrence.
- **[#642](https://github.com/Kuantor/kuantorflow/issues/642):** buttons in the edit dialog that translate a card's English examples into Ukrainian or Russian, off by default, offered only for the site's own written examples.

---

## Production

Read with `scripts/weekly_numbers.py` on PythonAnywhere on 28 October, for the window from 19 October. **Production was used almost only by us**: one account visited on each active day, and the largest single group of requests was a measuring script.

- **353 requests**: 336 answered 200, 4 redirected, 10 answered 304, and **none answered 404**, against 2 last edition. Three answered **405**, a page asked for with the wrong method, which this edition's numbers do not name. 221 were anonymous and 132 signed in.
- **Our own measurements again:** 154 requests were topic CSV downloads, the scripts that measured the public deck for #636 and the card-text tickets.
- **The home page:** median **754 ms**, p95 1.8 s, slowest 5.0 s with **11 database connections**. Last edition's median was 250 ms. The difference is the sample, not the code: this window's home pages were mostly signed in, with a median of 7 connections, where last edition's were mostly anonymous. No change in the window touched the home page, and the cost of those connections is what [#554](https://github.com/Kuantor/kuantorflow/issues/554) is for.
- **The Quiz's judging, in its first real use:** #612's check made **5 calls** on 19 October, about 680 input and 66 output tokens each, and the Quiz's slowest pages that morning took 3.1–3.7 s, the wait its PR measured. A word lookup took 3.3–4.3 s, as before.
- **AI usage:**
  - Haiku: 36 translations (about 490 input and 50 output tokens each, as last edition), 5 answer checks and one topic proposal.
  - Mykola: 9 calls. On 27 October 41,438 tokens came from the prompt cache against 16 sent uncached, so his caching works as designed.
- **Visits:** 23 visits over five days, all signed in, 222 active minutes and 56 pages; **2 accounts in all, none new**.
- **No admin emails were attempted.** There was no new account and no account near a limit, and the daily report's task is still unscheduled.
- **The alphabet check dropped nothing.**

The deploys themselves gave the rest:

- **The verb pairs:** 236 Ukrainian and 238 Russian forms looked up, with 131 and 195 partners found.
- **Topic strings:** 620 of 620 cards matched their topic's name before the drop; afterwards the check reports the column gone.
- **Limits:** production's `.env` sets none, so every ceiling is the code's own.

---

## Technical Highlights

**A removal asks the opposite question.** Every earlier migration was skipped when its object existed. A drop must be skipped when its object is *gone*, and it carries a guard, because it is the one step a redeploy cannot undo.

**Order the deploy by who still writes.** Reloading before the schema step meant no running code wrote to the column at the moment it disappeared. The runbook says so, because it is the reverse of every other deploy.

**Measure the failure you are preventing.** "Mykola would misfile topics" became "605 of 655 cards under their part of speech" once the old code was run against a database without the column.

**Derive the rule from the code.** The ceilings in `.env.example`, the white headings and the closed connections are each checked against what the code itself declares, so the next setting, page or helper is held to them without anyone remembering.

---

## Lessons Learned

1. **A test helper can turn one failure into a hang.** A connection left open by a failed statement blocked the fixture's cleanup indefinitely. Closing in `finally` is now checked across every database test file.

2. **`--limit` truncates silently — a third time.** The project board read back exactly 300 items, the limit passed, and looked as though none of this period's tickets were on it. With a higher limit there are 381, and all of them are.

3. **A proof can be narrow and still complete.** #210's thirteen breaks first ran nine test files each time and were on course for fifty minutes. Running, for each break, only the test that covers it took a minute and a half and proved the same thirteen things.

4. **A grep beats a list.** #210's own ticket named one maintenance script that read the column; searching found four, and a writer in `move_flashcard()` that a narrower search had skipped.

5. **The first real use finds the small things.** Both Quiz fixes came from Anton's own rounds on production within hours of the deploy, not from the suite.

---

## Plans

**Active development pauses here**, by Anton's decision, for other work in the final plan. Nothing is half-deployed: every merged change is live on PythonAnywhere, and no branch is open. When work resumes, the order agreed before the pause is the card-text work in [#632](https://github.com/Kuantor/kuantorflow/issues/632) — starting with #631, with a plain length limit now and the meaning-first choice later with #626 — then the video's follow-ups in [#628](https://github.com/Kuantor/kuantorflow/issues/628), and the request-safety tickets above.

### Should not wait for the pause to end

| | |
|---|---|
| kuantorflow#647 | CSRF protection, and `/logout` off `GET` |
| kuantorflow#620 | Say in the guide which parts an AI writes, and that they can be wrong |
| Tasks tab | Schedule the daily report (#593, shipped in code but never scheduled), so the admin emails start |
| Backups | The daily backup runs (17:00 UTC, seven kept, checked 28 October), but every copy is inside the PythonAnywhere account: keep one outside it |

### Highest Prio

Unchanged for a sixth edition.

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
| kuantorflow#632 | Card text: #617, #618, #620, #621, #629–#631, #642 and #110 |
| kuantorflow#628 | Follow-ups from the video: #624–#627 |
| kuantorflow#595 | Admin emails: #599 open |
| kuantorflow#560 | Observability: #554, #556, #559 and #563 open; #648 relates |
| kuantorflow#548 | Hardening Mykola's conversations: #545–#547, ai_agent#87, #89 and #91–#93 |
| kuantorflow#553 | Development process: #549, #551, #552 and #566's remaining items |
| kuantorflow#538 | Gaps from a teacher's perspective: #533–#537, #539 |

### Not on the board

| | |
|---|---|
| ai_agent#87, #89, #91–#93 | The five open Mykola hardening tickets, for a fourth edition |
| kuantorflow_automation#173 | The live smoke check still needs a kuantorflow checkout |

Every ticket filed in this period is on the board, in *Todo*.

### Board housekeeping

- ***In Progress*** still holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed, for a sixth edition; [#110](https://github.com/Kuantor/kuantorflow/issues/110) is there too and is part of #632.
- **Finished umbrellas:** [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) should close.
- **Superseded:** [#22](https://github.com/Kuantor/kuantorflow/issues/22) is replaced by #507.

---

*Report generated 29 October 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, and from production's numbers read on PythonAnywhere on 28 October with `scripts/weekly_numbers.py`, following the process recorded in `reports/README.md` (#252). Merged-PR window: 19 October 2026 (kuantorflow#635) through 27 October 2026 (kuantorflow#652), excluding the previous edition's own pull request (#633); kuantorflow_automation#239 through #247; ai_agent#97.*
