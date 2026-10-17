# KuantorFlow — Development Report

**Period:** 9 – 17 October 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**The site now tells its owner what happens on it.** The period's main thread came from Anton's own idea: emails about new accounts, heavy use and each day's activity. Four tickets under the new umbrella [#595](https://github.com/Kuantor/kuantorflow/issues/595) shipped. A shared mailer, verified live from PythonAnywhere, sends a notice on an account's first sign-in, a warning when an account reaches 80% of a daily limit, and a daily summary that sends nothing on a quiet day. A fifth ticket laid the ground for the next two: the browser now reports each visit, so time on the site and visitors can be counted, with robots left out. Around that came safer prompts, Mykola's recap button shown only when there is something to recap, three phone-layout fixes Anton found on his own phone, and fixes to the report converters themselves.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 15 |
| kuantorflow_automation | 15 |
| ai_agent | 1 |
| **Total** | **31** |

**Sixteen issues were closed, all of them completed**: fifteen in kuantorflow and [ai_agent#90](https://github.com/Kuantor/ai_agent/issues/90). None were declined or closed as duplicates. **Eighteen tickets were filed**, all in kuantorflow, and thirteen of them were also closed within the period.

The test suite grew from **2,779 to 3,042** offline tests, and from 2,967 to **3,245** with the database and live tiers included. Both tiers pass; three tests skip in the full run, as before.

*All 70 commits across the three repositories were authored by Kuantor. The 31 recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address. The other 39 all carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. Emails to the site's owner

Anton asked on 13 October for the site to email him about what happens on it. The umbrella [#595](https://github.com/Kuantor/kuantorflow/issues/595) split that into a shared piece and three notices, and all four shipped.

- **[#591](https://github.com/Kuantor/kuantorflow/issues/591): the mailer.** `notify.send_admin_email()` sends one plain-text email over SMTP. It is off unless the server's `.env` holds the SMTP settings, so local development needs no mail server. It never raises, and gives up after five seconds, so no sign-in or save can fail because of an email. Each attempt writes one line to a log of its own, `notify.log`, with the result and any error, but never the body or the password. Before any code, a standalone script confirmed that PythonAnywhere can reach Gmail on both ports. Google would not create an app password for the intended test account, so the emails come from Anton's own address to itself; the code did not change, only one setting. The first real email arrived on 15 October.
- **[#592](https://github.com/Kuantor/kuantorflow/issues/592): a new account.** On an account's first Google sign-in, Anton gets its number, a masked address (`an***@gmail.com`), the first name, the time and the commands to block it. A returning sign-in, or his own, sends nothing. Whether an account is new comes from MySQL's own count for the insert. That only works because the database driver does not ask for "found rows", and a test on a real server would catch it if that ever changed.
- **[#594](https://github.com/Kuantor/kuantorflow/issues/594): 80% of a daily limit.** The claim that takes an account past 80% of one of its five limits sends one warning, as an early sign of misuse. It lives in the one function every limit is claimed through, so no feature has code of its own. It fires once per account, limit and day without any stored state, and nothing about it can change whether the learner's action goes through.
- **[#593](https://github.com/Kuantor/kuantorflow/issues/593): the daily report.** A script on PythonAnywhere's Tasks tab reports the previous Kyiv day, 04:00 to 04:00. It lists each account's cards, rounds, answers, Mykola messages, lookups and the like, plus anonymous totals. Anton's own accounts never appear, so a day of only his use sends nothing and is logged as skipped. Nothing from a card or a chat is read, only counted.

**What production showed.** The mailer's one line in `notify.log` so far is the test email. No new account signed up and no account reached 80% of a limit after the notices were deployed, so none of them has fired yet. The daily task is to be scheduled.

### 2. Visits: time on the site, robots left out

**[#598](https://github.com/Kuantor/kuantorflow/issues/598)** is the foundation for two more of Anton's ideas: an email when a signed-in learner's visit ends ([#599](https://github.com/Kuantor/kuantorflow/issues/599)) and a daily count of all visitors ([#600](https://github.com/Kuantor/kuantorflow/issues/600)). Page loads could answer neither question. Reading a deck for twenty minutes is one request, an anonymous visitor had no identity, and a robot loads pages like anyone else.

- **A small script on every page** checks in about once a minute while the tab is visible, with the seconds the page was on screen. It waits for five seconds in front before the first check-in, so a glance is never a visit. A hidden tab sends what it has once and goes quiet.
- **The server** gives each browser a random visitor number in its session cookie and refuses robots by user agent. A check-in claims no limit and writes no request log line. One row per visit, continued while check-ins keep coming, can never gain more seconds than the time that passed, so two tabs side by side do not double it.
- **It changed a privacy promise.** The guide had said that without signing in almost nothing is tied to you. Its privacy section now says, in its own paragraph, that visits are counted, anonymous ones under a random number, and that they are deleted with an account.

The page script was tested as real code, run in Node with a fake clock, and checked in a real browser: 35 active seconds over a 36-second visit, one row, no request log line. Production's first reading, an hour after deploy, was one visit of 89 seconds.

### 3. Safer prompts and a safer Mykola

- **[#602](https://github.com/Kuantor/kuantorflow/issues/602): visitor text is data.** The four calls this repo makes to Claude each put text a visitor controls into the prompt: an uploaded notes line, a looked-up word, a topic idea, a generated text's request, and words other learners put in the deck. Each now goes in only inside its own tag, and each prompt says once that the tags hold data, never instructions. The notes splitter also checks in code that every term it gets back is part of its own line. Before and after, real Haiku calls ran on honest and crafted inputs. Honest results did not change, and Haiku resisted the crude injections even without the tags. So this is defence in depth, not a fix for an attack that worked.
- **[ai_agent#90](https://github.com/Kuantor/ai_agent/issues/90): never an empty reply.** When Mykola used up his tool rounds, he could answer with nothing at all. His last round now cannot call a tool, so it always writes an answer, and an empty reply is replaced with a sentence and logged.
- **[#584](https://github.com/Kuantor/kuantorflow/issues/584): a blocked account is told so.** Anton blocked a test account and saw Mykola's widget simply vanish, with no reason given. A banner under the header now says the account is blocked and how to ask for access back, on every page.

### 4. For learners

- **[#590](https://github.com/Kuantor/kuantorflow/issues/590): *Recap our last chats* only when there is an earlier chat.** A new learner saw the button at once, with nothing to recap. The page now tells the widget how many conversations are saved, the widget leaves out the one in progress, and the recap reads only the ones before it.
- **[#526](https://github.com/Kuantor/kuantorflow/issues/526): no dictionary fallback.** A word Oxford could not explain used to fall back to Reverso, which PythonAnywhere cannot reach. It now gets no explanation, locally as in production. Notes copied from Reverso still import.
- **Three phone fixes from Anton's own screenshots:** the upload popup's two halves are now equal ([#576](https://github.com/Kuantor/kuantorflow/issues/576)), its buttons stay one line each on a phone ([#578](https://github.com/Kuantor/kuantorflow/issues/578)), and the card editor's buttons sit in two tidy rows ([#580](https://github.com/Kuantor/kuantorflow/issues/580)). The last one also gained a hairline divider above the buttons and *Look up & update* moved to the left, each at Anton's request after he saw it.

### 5. Tooling

- **[#582](https://github.com/Kuantor/kuantorflow/issues/582): a guard against lost work.** `git checkout --` had silently thrown away finished work three times. A Claude Code hook now refuses the commands that discard uncommitted changes (`checkout --`, `restore`, `reset --hard`, `clean -f`), with 66 checks of its own.
- **[#586](https://github.com/Kuantor/kuantorflow/issues/586) and [#588](https://github.com/Kuantor/kuantorflow/issues/588): the report converters.** A fenced code block printed its language label and lost its line breaks, and a table cell could not hold a `|`, even escaped. Both were found while writing PDFs for Anton, and both now work as on GitHub.
- **[automation#221](https://github.com/Kuantor/kuantorflow_automation/pull/221):** `prove_fails.py` now names a failed check in a plain script by its assertion message.

---

## Production: a real week of numbers

The 9 October edition asked for a real week of data, and this is it: eight days on PythonAnywhere, read on 17 October.

- **166 requests since 9 October**: 124 answered 200, 4 answered 304, 4 redirected and 1 was refused. **33 answered 404**, none of them a route of the site. Most were **scanners looking for secrets**: `/public/.env`, `/server-config.json`, `/runtime-config.json`, `/settings.js` and `/v1/models`, the path of an AI service's API. Only two were `/favicon.ico`. None of them can reach anything: the site serves files only from `static/`, and the real `.env`, `/static/../.env` and its encoded forms all answer 404. 105 came from anonymous visitors and 61 from signed-in ones.
- **The home page's median rose from 440 ms to 835 ms, and its connections from 2 to 5.** That is the mix, not new code. Measured locally, the anonymous home page opens 2 connections and the signed-in one opens 5: the same 2, one more for the block check, and two for the review badge. Last edition's sample was mostly anonymous; this one has real signed-in use. The two slowest home pages, at 3.1 and 4.7 s, opened 10 and 6 connections. That points [#554](https://github.com/Kuantor/kuantorflow/issues/554) (reusing connections) at the signed-in home page.
- **The slowest requests were the paid ones**: a word lookup at 5.3 s (two Haiku translations and a dictionary) and a generated text at 4.3 s. Mykola's streamed answer starts after a median of 488 ms. *Flashcards* answered in 355 ms.
- **The first Haiku calls**: 9 in all. Four word translations used 1,838 input and 177 output tokens, about 460 and 44 per call, as measured on 9 October. Four notes splits used 596 and 140, and one generated text used 354 and 227. So [#563](https://github.com/Kuantor/kuantorflow/issues/563) finally has a small baseline. **Mykola** made 9 calls, with 573 output tokens, 8,021 uncached input tokens, 21,686 written to the cache and 20,796 read from it.
- **The alphabet check has still dropped nothing** in production (no `TRANSLATE-DROPPED` lines).

---

## Technical Highlights

**Never in the way.** Every notice, the visit check-in and the mailer itself catch their own failures. A sign-in, a save or a paid claim answers the same whatever happens to an email or a visit row.

**Nothing to report, nothing sent, and say so.** A quiet day sends no email but writes a `skipped` line. A missing email can then be told from a failed one.

**Count, don't quote.** The daily report counts rows in SQL and reads nothing from a card or a chat. Every notice names a learner by account number and a masked address.

**Data, not instructions.** Visitor text reaches a prompt only inside a tag, and where a reply's shape can be checked in code, it is.

**A time can't exceed the time that passed.** A visit gains at most the wall-clock time since its last check-in, so neither a second tab nor a script can inflate it.

**The privacy section moves with the code.** The emails and the visit count each changed what the guide says the site keeps. Each change landed in the same pull request as its code.

---

## Lessons Learned

1. **A test can hide the bug it was written for.** #593's real-MySQL test let a time-zone bug through: a window shifted by three hours lost a card at one end and gained one at the other, so the total stayed at 2. Putting the edge cards on different accounts made the shift visible. `prove_fails.py` is what found it.

2. **Make the server's setup explicit.** The same test passed partly because of how the local MySQL is set up. It now hands every connection an explicit time zone, so the result no longer depends on the machine.

3. **Measure before calling it a fix.** #602's crude injections failed even without the tags. The before-and-after run says what the change is (defence in depth) and shows that honest results did not change.

4. **Configuration, not code.** Google refused an app password for the new sender account. Changing the sender was one line in `.env`, because the mailer sends from whatever is configured.

5. **Check a ticket's premise against the code.** #598's ticket asked for a 20-second rate limit. Capping each check-in by the time elapsed did that job better, without dropping a hidden tab's last report.

6. **Read production, not a copy of it.** On the local checkout every 404 was the favicon, which nearly replaced this report's first guess. Production's own paths settled it: most were scanners looking for secret files.

7. **Escapes typed into a tool call still arrive decoded.** `\n` landed as real line breaks twice this week, in #592 and #590. Building the backslash with `chr(92)` avoids it.

---

## Plans for Next Week

**This edition's production numbers took five commands, pasted back one by one.** At Anton's suggestion, [#607](https://github.com/Kuantor/kuantorflow/issues/607) turns them into one: `scripts/weekly_numbers.py` writes everything to a single file and adds the paths that answered 404. It is in review as [#608](https://github.com/Kuantor/kuantorflow/pull/608), and the next edition starts from it.

**Then the last two emails.** [#599](https://github.com/Kuantor/kuantorflow/issues/599) (how long a signed-in learner spent, emailed when the visit ends, from an hourly task) and [#600](https://github.com/Kuantor/kuantorflow/issues/600) (a daily count of visitors) both read #598's visits, which are now being recorded. The daily report's task is to be scheduled on the Tasks tab. The 9 October edition's first items, #554 and #563, now have data. The signed-in home page is #554's target, and the first Haiku calls give #563 a baseline. [#194](https://github.com/Kuantor/kuantorflow/issues/194) (copyright-safe images) has a decision waiting: no hotlinked images, and any future photo downloaded, hosted on the site and credited.

### Highest Prio

Unchanged for a fifth edition.

| | |
|---|---|
| kuantorflow#100 | Quiz: accept either aspect of a verb (писати / написати), from a stored pair table |
| kuantorflow#144 | Fill the missing translation on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella — **complete; should close** |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### Umbrellas

| | |
|---|---|
| kuantorflow#595 | Admin emails: #591–#594 and #598 done; #599 and #600 open |
| kuantorflow#560 | Observability: #554, #556–#559 and #563 open |
| kuantorflow#548 | Hardening Mykola's conversations: #545–#547 here, ai_agent#87, #89 and #91–#93 (#90 done) |
| kuantorflow#553 | Development process: #549, #551, #552 and #566's remaining items |
| kuantorflow#538 | Gaps from a teacher's perspective: #533–#537, #539 |

### After the demo: modernising the UI

[#507](https://github.com/Kuantor/kuantorflow/issues/507) and its four tickets ([#503](https://github.com/Kuantor/kuantorflow/issues/503)–[#506](https://github.com/Kuantor/kuantorflow/issues/506)) are unchanged.

### Not on the board

| | |
|---|---|
| ai_agent#87, #89, #91–#93 | The five open Mykola hardening tickets: summarise long chats, card text as data, retrieval in Ukrainian and Russian, the reply language, an evaluation suite |
| ai_agent#90 | Done this period, and never on the board |
| kuantorflow_automation#173 | The live smoke check still needs a kuantorflow checkout |

### Board housekeeping

- ***In Progress*** still holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed, for a fourth edition.
- **Finished umbrellas:** [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) should close. #487 waits only on [#481](https://github.com/Kuantor/kuantorflow/issues/481), from late October.
- **Superseded:** [#22](https://github.com/Kuantor/kuantorflow/issues/22) is replaced by #507.
- **Conflicting:** [#430](https://github.com/Kuantor/kuantorflow/issues/430) and [#459](https://github.com/Kuantor/kuantorflow/issues/459) still contradict each other. #526 is now done and settles neither.

---

*Report generated 17 October 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, and from production's logs and database read on PythonAnywhere the same day, following the process recorded in `reports/README.md` (#252). This edition covers nine days. Merged-PR window: 10 October 2026 (kuantorflow#575) through 17 October 2026 (kuantorflow#606), excluding the previous edition's own pull request (#574); kuantorflow_automation#217 through #231; ai_agent#96.*
