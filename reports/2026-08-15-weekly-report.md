# KuantorFlow — Weekly Development Report

**Period:** 9 – 15 August 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This period **turned the deck into something you can play with, and made Mykola quick enough to talk to**. The word-games cluster that had been specified for weeks went from four unbuilt tickets to a games panel on the front page, a topic picker every activity shares, and two finished games; and Mykola — who could previously only be waited for — now streams his answer as he writes it, types it out, and can be asked to think faster. **42 pull requests merged** and the regression suite grew from **706 to 936 passing offline tests**, plus the opt-in database and live tests, which take the full run to 1,016.

The week's most useful result was a measurement rather than a feature. Mykola's latency had been discussed for months in terms of "he feels slow"; running six short questions against the real API at three settings produced numbers that immediately settled the design: **3.17 s to his first word and 7.04 s to his last**, of which the first four seconds were deliberation and roughly half the remainder was him writing 500 characters where 60 would do. Those two halves needed two different levers, and neither would have been found by reasoning about the code.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | The games chassis and the first two games; Mykola's streamed answer, typewriter and fast thinking; the session cookie | 21 |
| **kuantorflow_automation** | A parallel test PR for every change; suite → 936 offline | 14 |
| **ai_agent** | Deck-reading tools, conversation caching, and the latency work | 7 |
| **Total** | | **42** |

**21 issues closed — 20 as completed, one as not planned.** The exception is ai_agent#70 (paging the deck ten cards at a time), closed deliberately once #73 made the read small enough that paging had nothing left to solve. Among the completed is **#252, the auxiliary ticket asking that the weekly report's own process be written down**; this edition is the first produced from that document rather than from memory.

*Per the standing request, commit authorship was checked across all three repositories: all **104 commits** were authored by Kuantor, with Claude Opus 5 as co-author on 62 of them. **No GitHub Copilot commits** were found in this range. One grep match appeared and was inspected: it is the commit that documents this very check (#252), not a Copilot commit.*

---

## Completed Work by Theme

### 1. The word games became a place you can go

The games had been specified in #129 and scoped in #233, and none of them had anywhere to live. That was fixed in dependency order, each step needing the one before it:

- **The groundwork** (#248, PR #249) — fetching cards from several topics in one query, and one pure function that answers "which topics does this round play over" for repeated `?topic=` parameters, a remembered selection, and no parameter at all. An empty topic list returns *no* cards rather than every card, which is the same trap the owner filter already documents.
- **The topic picker** (#250, PR #251) — grouped under section headings in the order the front page shows them, with tri-state "select section" boxes, a running card total, and a Start button that explains itself when the selection is too thin. The quiz proved it by becoming the first activity to run over several topics at once, on a **separate endpoint** from `/quiz/<topic>`, because with both shapes on one endpoint `url_for` picks the path converter and the multi-topic URL becomes unbuildable.
- **The ways in** (#253, PR #255) — a games panel and a reader panel on the front page, and a topic-page activity row that wraps properly on a phone. All three render from **one declaration**, `games.ACTIVITIES`, so adding an activity is one entry rather than one entry and three templates.
- **Two games finished** — *Real or fake* (#132, PRs #257/#259), where the invented words come from a character n-gram trained on the whole visible deck, and *Scrambled* (#133, PR #256), which holds the first and last letters and **refuses to ask a word it cannot disturb**: `cat` has no middle and `noon` shuffles to itself, and printing either would be printing the answer.
- **Honest tiles** (#261, PR #262) — a game that is still a stub is greyed, driven by the single `ticket` field on its declaration, so a game becomes real by deleting one field.
- **Ten words a round, not twenty** (#263) — changed after watching a quiz over the whole curriculum turn into 93 typed answers.

Icons for the six buttons landed as WebP through a script whose sizes are measured against the eighteen topic icons rather than chosen (#234, PR #247).

### 2. Mykola learned to read the deck

Mykola could write cards but not look at them. Five tickets in ai_agent gave him tools that read (#68, #73, #75, #77, with kuantorflow#242 injecting the readers), and the design decisions are worth more than the feature: **words first, one card on demand**, because a topic's worth of full cards is a wall of context for a question that usually needs a list; **no translations by any route**, because he is a native English speaker and does not need a card to tell him what a Ukrainian word means; and **every read logged** (#75), so which tool he reaches for became observable rather than assumed. #77 then made him explain the gap between a topic's card count and its word count instead of quietly showing two different numbers.

Conversation caching (#71) extended the cached prefix from the system prompt to the conversation itself — the one part of the request that grows every turn and was still being paid for at full price.

### 3. Mykola answers faster (ai_agent#50)

Three changes, in the order they were found:

- **Streaming** (ai_agent#79, PR #276) — the agent had streamed from the Anthropic API since it was written, but the fragments were joined into a string before anything outside the call could see one. The only consumer was the command line; the browser waited for the finished answer. `stream_answer()` now yields them and a Server-Sent Events endpoint carries them to the widget.
- **A typewriter** (PR #277, fixed in #279) — paced by *how much text is waiting* rather than by a constant, so a chunk landing while the previous one is still being typed speeds the typing up and the two run together.
- **Fast thinking** (ai_agent#80, PR #278) — a Settings switch that lowers deliberation and asks for shorter replies. On by default, because a learner typing into a corner of the page is asking for an answer rather than an essay.

Measured over six short questions, two runs each, against the real API:

| setting | first word | full answer | reply length |
|---|---|---|---|
| before | 3.17 s | 7.04 s | 521 characters |
| less deliberation only | 1.75 s | 5.65 s | 518 characters |
| **fast thinking (shipped)** | **0.98 s** | **1.98 s** | **63 characters** |

`_log_usage()` now records the wait beside the tokens, so the next question about speed is answered from the log rather than from a throwaway harness.

### 4. Guards, chrome and one security fix

- **The session cookie** (#274, PR #275) carries the keyword gate pass and the signed-in identity, and had none of its protective flags set. It is now `Secure`, `HttpOnly` and `SameSite=Lax` — deliberately not `Strict`, which would withhold the cookie on Google's own callback navigation and break sign-in silently.
- **An anonymous chat now survives signing in** (#240, PR #241) instead of being wiped at the moment the learner commits to an account.
- **Copying one of Mykola's replies** (#245, PR #246), a button on hover and a tap on touch.
- **The Settings action row** stopped overflowing on a phone (#238, PR #239), and Mykola's own log lines finally have somewhere to be written (#243, PR #244).

### 5. Process, and the report that documents itself

**#252 — "Document how a weekly report is produced" — was completed** (PR #254). It records the cutoff rule (the previous report's own footer names its window, and there is nowhere else to look it up), the exact `gh` and `git` commands that gather each figure, the standing Copilot-authorship check, the section order, and the trap that once committed a report as a one-page PDF of the browser's own error page. **This edition is the first written from it**, and the process held: every figure here was gathered by those commands rather than recalled.

Alongside it, the test suite was tidied before the last four games (automation#69, PR #70) — shared fixtures, a split file, and a lifecycle that maintains itself — and CLAUDE.md gained the games chassis (#264), so the next contributor does not have to infer it from the code.

### 6. Wave two, specified but not started

Nine tickets were written for the four activities chosen out of #236 — **#265** as the umbrella, #266–#268 technical, #269–#272 the games, #273 the icons — along with #258 and #260, which came out of shipping *Real or fake* and record where a generator alone cannot close the gap. None of this is merged work; it is next week's, and it is fully specified.

---

## Technical Highlights

**One declaration, several surfaces.** `games.ACTIVITIES` is rendered by the front-page panel, the picker and the topic-page row. The alternative — three lists that must agree — is the shape that goes stale, and the same argument produced `_mykola_chat_inputs()` when a second chat endpoint appeared: every rule about whether a message may be answered lives in one function, because a second copy is the one that rots.

**Measure before choosing a number.** Twice this week a plausible change would have achieved nothing. Lowering `effort` alone left the reply length untouched at 518 characters against 521 — it buys deliberation time, not brevity — and a fixed typing speed would have stuttered against an arrival rate that is not fixed. Both were settled by running the thing and reading the numbers.

**The seam that was already there.** Streaming did not need to be built; it needed to be carried across one boundary. Reading the code first turned a feature into a plumbing job.

**Feature detection between repositories.** kuantorflow asks the installed agent's signature what it supports before passing anything new, and the streaming endpoint answers 404 when the agent cannot stream, so the widget falls back. The two repositories deploy in either order, and neither may assume the other is current.

**Claim only what was verified.** The browser preview does not composite frames, so the typewriter was never actually watched here; the pacing was replayed arithmetically and the PRs say so. That the animation nevertheless failed in exactly that environment is what exposed the real bug below.

---

## Lessons Learned

1. **The obvious browser API was the wrong one.** `requestAnimationFrame` does not run in a tab that is not on screen, so an answer arriving while the learner reads another tab would sit half-typed forever — and the closing event would never apply, losing the sources and a saved card's deck refresh with it. It was found by measuring, not by reasoning, and the fix is a timer plus an explicit "nobody is watching" branch.

2. **A streamed response changes the rules around it.** Once the first byte is out, the status code is fixed at 200 and a session write never reaches the browser. The anonymous free-message counter would have quietly stopped counting; claiming the quota *before* the stream opens is now pinned by a test that exists for exactly that reason.

3. **Two halves of a wait need two levers.** The evidence is the middle row of the table above, and it is the sort of thing that is obvious only once measured.

4. **A destructive git command cost an hour.** `git reset --hard` was run to sync a repository while uncommitted work was in the tree; the preceding `git checkout` had aborted, and the reset landed on the branch holding the work. Everything was replayed from context, and the practice is now to stash and branch straight off `origin/main`, which cannot fail that way.

5. **An outside offer is judged against the ticket, not against its own description.** A vendor offered to demonstrate a metered PDF-to-Markdown service against #140. Their own list of what the test would *not* claim — parser integration, dedupe, reading order, PythonAnywhere compatibility — was essentially that ticket's deliverables, leaving only the part nobody doubts. Declined, courteously, with the reason recorded.

---

## Plans for Next Week

### Highest Prio

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#185](https://github.com/Kuantor/kuantorflow/issues/185) | Images for words and topics |
| kuantorflow | [#216](https://github.com/Kuantor/kuantorflow/issues/216) | Descriptions for topic sections |
| kuantorflow | [#194](https://github.com/Kuantor/kuantorflow/issues/194) | Copyright-safe image uploads |
| kuantorflow | [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective/imperfective answers as equal (AI) |
| ai_agent | [#61](https://github.com/Kuantor/ai_agent/issues/61) | Show the time of messages in the chat |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend card meanings |

### In Progress

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#130](https://github.com/Kuantor/kuantorflow/issues/130) | Levenshtein multiple-choice translation game |
| kuantorflow | [#131](https://github.com/Kuantor/kuantorflow/issues/131) | Keyboard-adjacency typo distractors (MCQ) |
| kuantorflow | [#110](https://github.com/Kuantor/kuantorflow/issues/110) | Investigation: Learner's vs Collegiate dictionary |

### Nice To Have

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#94](https://github.com/Kuantor/kuantorflow/issues/94) | Spaced-repetition review mode (SM-2) |
| kuantorflow | [#129](https://github.com/Kuantor/kuantorflow/issues/129) | Smart word-manipulation techniques for exercises |
| kuantorflow | [#85](https://github.com/Kuantor/kuantorflow/issues/85) | Full-screen widget mode |
| kuantorflow | [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign the website for a modern look |
| kuantorflow | [#88](https://github.com/Kuantor/kuantorflow/issues/88) | A docx describing how to run backup/restore |
| kuantorflow_automation | [#4](https://github.com/Kuantor/kuantorflow_automation/issues/4) | Automated tests for the Mykola AI agent |

### Todo (selected)

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards between local and remote databases |
| kuantorflow | [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme for the website |
| kuantorflow | [#84](https://github.com/Kuantor/kuantorflow/issues/84) | Darken the main page behind the welcome popup |
| kuantorflow | [#75](https://github.com/Kuantor/kuantorflow/issues/75) | About popup: the widget overlaps it |
| kuantorflow | [#76](https://github.com/Kuantor/kuantorflow/issues/76) | Scheduled recaps with random log selection |
| ai_agent | [#47](https://github.com/Kuantor/ai_agent/issues/47) | Audit and remove agent code the embedded widget does not need |
| ai_agent | [#49](https://github.com/Kuantor/ai_agent/issues/49) | Runner setting for the number of last logs |

### Human

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#147](https://github.com/Kuantor/kuantorflow/issues/147) | More accurate classification of words in the "General" topic |
| kuantorflow | [#236](https://github.com/Kuantor/kuantorflow/issues/236) | Ten researched word-activity candidates — four promoted, six recorded |
| ai_agent | [#56](https://github.com/Kuantor/ai_agent/issues/56) | Make Mykola's avatar bigger |

### Filed this week, not yet on the board

The best-specified work available, and the reason the board understates what is ready to start:

| Issue | Title |
|---|---|
| [#265](https://github.com/Kuantor/kuantorflow/issues/265) | **Word games, wave two** — umbrella for the four activities chosen from #236 |
| [#266](https://github.com/Kuantor/kuantorflow/issues/266) | A round says what it needs: card-level eligibility, and a minimum number of topics |
| [#267](https://github.com/Kuantor/kuantorflow/issues/267) | One typed-answer path: normalise once, grade the questions that were asked |
| [#268](https://github.com/Kuantor/kuantorflow/issues/268) | The app's first audio: a shared browser speech helper |
| [#269](https://github.com/Kuantor/kuantorflow/issues/269) | Game: odd one out |
| [#270](https://github.com/Kuantor/kuantorflow/issues/270) | Game: spell it |
| [#271](https://github.com/Kuantor/kuantorflow/issues/271) | Game: rebuild the sentence |
| [#272](https://github.com/Kuantor/kuantorflow/issues/272) | Game: listen and type |
| [#273](https://github.com/Kuantor/kuantorflow/issues/273) | Icons for the wave-two game buttons |
| [#258](https://github.com/Kuantor/kuantorflow/issues/258) | Real or fake sometimes invents a real word |
| [#260](https://github.com/Kuantor/kuantorflow/issues/260) | A table of generated distractors |
| [#233](https://github.com/Kuantor/kuantorflow/issues/233) | Word games: the umbrella for wave one, still open on #130/#131/#235/#237 |
| [#235](https://github.com/Kuantor/kuantorflow/issues/235) | Game: fill the gap |
| [#237](https://github.com/Kuantor/kuantorflow/issues/237) | Activity: generate a text from your own words |

**#266 is the one to start with.** It registers all four wave-two activities as stubs, so their tiles, picker entries and topic-page links appear — greyed — before any round exists, which is how wave one landed and what makes progress visible while it is in flight.

---

*Report generated 15 August 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). Merged-PR window: 9 August 2026 (kuantorflow#239, kuantorflow_automation#65, ai_agent#69) through 11 August 2026 (kuantorflow#279, kuantorflow_automation#79, ai_agent#80).*
