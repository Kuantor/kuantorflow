# KuantorFlow — Development Report

**Period:** 10–14 September 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

A short period — four working days rather than the previous edition's fortnight — and an unusually focused one. Nearly everything in it points at a single event: the app is being shown to English teachers next week, and the work was chosen by what that audience would notice rather than by engineering value.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 10 |
| kuantorflow_automation | 8 |
| ai_agent | 2 |
| **Total** | **20** |

**Fifteen issues were closed, fourteen as completed and one as not planned.** The declined one is [#147](https://github.com/Kuantor/kuantorflow/issues/147), and it was declined for the right reason: the problem it describes was solved by two changes made outside it, and a walkthrough of the deployment confirmed from the outside that nobody can reach the cards it worried about.

Three of those closures were housekeeping discovered while writing this report. **#19, #208 and #424 had all shipped and were all still open**, because GitHub's automatic linking acts only on the *first* reference in a phrase like "Closes #227, #424 and #208". They are closed now, each with a comment naming the pull request that delivered it.

The test suite grew from **1,996 to 2,062** offline tests, and from 2,142 to **2,209** with the database and live tiers included. Both tiers pass.

*All 49 commits across the three repositories were authored by Kuantor; 29 carry a `Co-Authored-By: Claude Opus 5` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. Preparing for the teacher demo

Two documents now live in `reports/`. The **demo plan** ([#420](https://github.com/Kuantor/kuantorflow/issues/420)) holds the runbook for the day, what to show a teacher and in what order, the week's work in priority order, and — as importantly — what was deliberately *not* being done and why. The **production walkthrough** ([#427](https://github.com/Kuantor/kuantorflow/issues/427)) records the first end-to-end pass over the deployed site since the last three releases.

That walkthrough is the most valuable thing in this period and it produced no code at all. It confirmed that the word lookup returns an explanation, an example and both translations; that one generated passage used fourteen deck words and its worksheet came out at fourteen blanks with a matching key; that all ten activities deal real rounds; that Mykola answers accurately about an activity, citing the user guide. It also fetched the deployed stylesheet and confirmed every rule from this period is actually in it — the check for [#300](https://github.com/Kuantor/kuantorflow/issues/300)'s failure mode, where a cached stylesheet renders a *plausible wrong page* rather than a broken one.

The earlier verification of the printable worksheet belongs here too: rendered at the documented worst case of forty blanks over 865 words, it came out at four pages with every blank intact, no blank split across a page break, and the answer key alone on the last sheet.

### 2. Failures that used to be silent

[#99](https://github.com/Kuantor/kuantorflow/issues/99) asked the app to check the Anthropic credit balance when somebody enters the site. It was built the other way round, and the ticket's premise was corrected in the process: a balance cannot be read without spending a call, so checking on entry would cost money on every page load to learn something that only matters when a paid feature is used — and would still be stale a minute later. Instead `billing.py` holds one question (*is this failure the money?*) and one sentence answering it, with the address to write to. The chat already had its own version of that check in `ai_agent`, so the phrase list is deliberately shared rather than reinvented.

[#19](https://github.com/Kuantor/kuantorflow/issues/19) put a tip above the lookup box, because the front page offers three things at once and said which to start with nowhere.

### 3. Popups: one policy, arrived at in three steps

Four tickets closed here and the order they arrived in is the story. [#75](https://github.com/Kuantor/kuantorflow/issues/75) asked for the About popup to stop being covered by the chat widget — but the obvious fix, raising the shared overlay, would have silently undone [#296](https://github.com/Kuantor/kuantorflow/issues/296), which put the widget *above* dialogs on purpose so a learner can ask about the card in front of them. So only About moved, and the stacking order — decided in six separate rules, each knowing only its own number — was written down in one place.

[#227](https://github.com/Kuantor/kuantorflow/issues/227) widened the cramped edit popup, and the ticket's own analysis turned out to be the valuable part: the dialog had asked for 560px and rendered at 420 for months, because another rule set the same property at the same specificity seven hundred lines later. Raising 560 to 700 would have changed nothing while looking exactly like a fix.

That widening then caused [#424](https://github.com/Kuantor/kuantorflow/issues/424) — the 700px dialog slid under the chat panel and took *Cancel* with it — which was fixed by moving the dialog aside rather than changing the layer, reusing the page's existing `--mykola-space` declaration. And a report from a phone produced [#425](https://github.com/Kuantor/kuantorflow/issues/425): below the width where a dialog can move aside, the widget now hides while any dialog is open. The two breakpoints meet exactly, and a test asserts they stay that way, because a gap between them would be a band of widths where neither happens.

[#84](https://github.com/Kuantor/kuantorflow/issues/84) closed as already done, confirmed on the running app rather than assumed, and [#208](https://github.com/Kuantor/kuantorflow/issues/208) removed a caption the front page said twice.

### 4. One declaration, three times

[#412](https://github.com/Kuantor/kuantorflow/issues/412) moved `ANTHROPIC_API_KEY` off Mykola's import path. The key had been arriving through the agent's `.env`, which meant the word lookup, the generated text and the topic builder all depended on a repository none of them uses — and a failed agent import would have taken all three down without a word.

[#414](https://github.com/Kuantor/kuantorflow/issues/414) was the same shape with a sharper edge. The default topic was the string `"general"` typed into six places across two repositories, so [#407](https://github.com/Kuantor/kuantorflow/issues/407)'s rename reached the database and never reached the code: every lookup saved with an empty topic box quietly recreated the topic that had just been consolidated away.

[#416](https://github.com/Kuantor/kuantorflow/issues/416) applied the lesson before it could cost anything. Six rounds grade an answer server-side; five read back what they asked through `games.asked()` and the sixth — *Multiple choice*, the most played — kept its own inlined copy. They now grade in one place, which is where [#338](https://github.com/Kuantor/kuantorflow/issues/338)'s recall write will go.

### 5. ai_agent is active again

Two pull requests, ending a silence that had run for three editions. [ai_agent#82](https://github.com/Kuantor/ai_agent/issues/82) fixed a retrieval fault nobody had a ticket for until it was noticed in conversation: Mykola could not describe *Fill the gap*, because a heading was weighted like prose and the name of a thing scored no higher than the words around it. Only seven of ten activities were reachable by name. ai_agent#84 is the other half of #414 — the host now tells the agent where an untitled card goes, with the injection feature-detected so either repository can deploy first.

---

## Technical Highlights

**A rule that lives as several copies is not a rule.** Three separate tickets this period were the same defect in different clothes: a value typed in six places (#414), a key arriving through a repository that does not own it (#412), and a loop duplicated in the one round that matters most (#416). In each case nothing was broken until something changed, and then the change reached one copy.

**The browser found two bugs the suite could not.** The first-run tip set its `hidden` attribute and stayed on screen, because a class selector beats the browser's own `[hidden] { display: none }` — every markup assertion passed throughout. The second was the dialog overlap, visible only once a real panel and a real dialog were open at real sizes. Both were found by driving the deployed page and reading computed style, which is now the fourth time that class of defect has shipped green.

**Tests assert relationships, not values.** The new suites compare two z-indexes read out of the stylesheet rather than restating a number; they assert that the hiding breakpoint meets the shifting breakpoint rather than writing 1240 twice; they assert that a selector *can win* rather than that it says 700px. A test holding the literal would have frozen the very value it was written to guard.

**Verification beats construction near a deadline.** The single most useful piece of work in these four days produced no commits: walking the deployment as a teacher would, which confirmed the app is ready and answered an open ticket for free.

---

## Lessons Learned

1. **The premise of a ticket is worth checking before its solution.** #99 asked for a credit check on entry, #227 looked like a number that needed raising, and #147 described a problem that two unrelated changes had already removed. All three were right about the symptom and wrong about the cause.

2. **`git checkout --` discarded a finished fix again.** The repository's own guidance says to commit before deliberately breaking something to prove a test fails, because the restore step discards everything uncommitted in that file. It was skipped once here and cost the change being verified — the fourth time in this project.

3. **"Closes #a, #b and #c" closes only #a.** Three shipped tickets sat open for a day because of it. Worth one line per reference in a PR body, or a check when writing the report.

4. **`--limit` truncates silently, and the default order is not the order you are filtering on.** The first pass at the closed-issue list for this report missed four tickets, because old issues closed recently fall outside the newest-N window. The repository's own report process warns about this; the warning is there because it has happened before.

5. **A feature landing can break its neighbours.** Widening the edit dialog (#227) created #424 within a day, and #424's fix created the conditions for #425. None of the three was a mistake; together they are the argument for writing a *policy* down — which is what the stacking-order map in the stylesheet now is.

---

## Plans for Next Week

The demo comes first, and almost nothing on the board is part of it. The plan in `reports/2026-09-13-demo-plan.md` holds the runbook; the material to send with the link — a narration script aimed at teachers and a written set of answers to the questions a teacher actually asks — is prepared outside the repository.

### Highest Prio

| | |
|---|---|
| kuantorflow#100 | Quiz: treat perfective and imperfective answers as equal |
| kuantorflow#144 | Fill missing translations on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### In Progress

| | |
|---|---|
| kuantorflow#110 | Learner's vs Collegiate dictionary — now also the blocker for a Merriam-Webster that works at all |
| kuantorflow#396 | Claiming unowned cards — **shipped; the board is stale here** |

### Filed this period, and all on the board

| | |
|---|---|
| kuantorflow#418 | Split `app.py` — 4,917 lines in one file |
| kuantorflow#429 | Refill the *General knowledge* cards from the dictionary and translator |
| kuantorflow#430 | Settings: offer only the providers that work, explain the rest behind a link |
| kuantorflow#431 | Settings: pin *Cancel* and *Save*, move the account actions out of that bar |

Ticket #431 subsumes [#120](https://github.com/Kuantor/kuantorflow/issues/120), which was measured this period rather than built: on a phone the Settings dialog is about 2,470px tall, and scrolling to the bottom puts its close button 1,657px above the viewport. Both are the same structural change, and the pattern for it already exists in the stylesheet and was simply never adopted by that dialog.

### Decisions outstanding

Two questions on #338 still gate its first phase — what the recall row is keyed on, and whether the schedule runs on verified results or on self-rating. [#260](https://github.com/Kuantor/kuantorflow/issues/260) answered the first of those in the opposite direction for its own table, which is the reason it should be decided deliberately rather than inherited.

---

*Report generated 14 September 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). This edition covers four days rather than a week, timed to precede the teacher demo. Merged-PR window: 11 September 2026 (kuantorflow#411) through 14 September 2026 (kuantorflow#428); kuantorflow_automation#144 through #151; ai_agent#83 and #84.*
