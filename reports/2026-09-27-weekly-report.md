# KuantorFlow — Development Report

**Period:** 14–27 September 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow) · [ai_agent](https://github.com/Kuantor/ai_agent) · [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

**The site is public.** On 20 September the keyword gate came off ([#199](https://github.com/Kuantor/kuantorflow/issues/199)), and anybody with the address can now look words up, play the games and talk to Mykola. That was the goal of [#331](https://github.com/Kuantor/kuantorflow/issues/331), and most of this fortnight was the work that had to exist *before* it could safely happen: a signing key nobody else knows, a daily ceiling on every paid action, a `robots.txt`, and a database check that stopped announcing its own credentials. The week before that went to splitting `app.py`, 4,917 lines in one file, into six modules. The days after it went to what a public site shows its visitors: a Help page, a steadier Settings dialog and a phone layout repaired.

| Repository | Merged PRs |
|---|---|
| kuantorflow | 25 |
| kuantorflow_automation | 27 |
| ai_agent | 0 |
| **Total** | **52** |

**Twenty issues were closed: seventeen completed, one not planned and two as duplicates.** The declined one is [#451](https://github.com/Kuantor/kuantorflow/issues/451), which would have removed the *Home* link from the header because the banner already goes home. Nobody can tell that the banner is a link, so removing the only visible way home would have made the header less clear, not more. What the ticket really found was a missing hover state, and that is now [#478](https://github.com/Kuantor/kuantorflow/issues/478). The duplicates are [#94](https://github.com/Kuantor/kuantorflow/issues/94), the second copy of the spaced-repetition ticket, and [#465](https://github.com/Kuantor/kuantorflow/issues/465), which asked for the same pinned Settings buttons as #431. #465 had shipped with #431 and was still open; it was found and closed while gathering the figures for this report.

The test suite grew from **2,062 to 2,273** offline tests, and from 2,209 to **2,435** with the database and live tiers included. Both tiers pass.

*All 111 commits across the three repositories were authored by Kuantor (the six recorded under the full name Anton Kuznietsov are pull requests merged through GitHub's web page, from the same address). 54 carry a `Co-Authored-By: Claude` trailer. No GitHub Copilot commits were found in any repository.*

---

## Completed Work by Theme

### 1. Opening the site: limits first, gate second

The keyword was never an authorisation; it was one string, handed out by hand, that anyone holding it could pass on and nobody could revoke. What it actually bought was a **rate limit by obscurity**, and that is what had to be replaced first, with no window in which the open internet had an uncapped line to the Anthropic account. So #199 shipped **last**, after six tickets that each closed one gap:

- **[#445](https://github.com/Kuantor/kuantorflow/issues/445): the session cookie could be forged.** `SECRET_KEY` fell back to a value published in this repository, and the cookie is signed, not encrypted, so a visitor could write one claiming to be the admin. The app now refuses to start without a key. Only `python app.py` falls back to a *random* key for that run, so a misjudged deployment fails as "sessions do not survive a restart", which is visible and harmless, rather than as "sessions anybody can forge".
- **[#447](https://github.com/Kuantor/kuantorflow/issues/447): signing in removed three limits instead of raising them.** Mykola's chat, the welcome-back recap and the notes upload had no per-account ceiling. Part one merged the three usage tables into one, `action_usage`, where every claim is a single all-or-nothing statement. Part two gave each action its ceiling.
- **[#456](https://github.com/Kuantor/kuantorflow/issues/456): three pools for every paid action.** Each paid action now has your pool, the anonymous visitors' pool and the site-wide pool, claimed in that order. Before this, generation was inverted: measured, twelve anonymous texts meant a thirteenth request from an account that had spent nothing was refused. The ceilings are now **cost-weighted**: one chat message on Opus costs about thirty generated texts on Haiku, so the chat ceilings are the tight ones. At the defaults, every pool emptied every day comes to roughly $8.
- **[#458](https://github.com/Kuantor/kuantorflow/issues/458): `robots.txt`**, shipped while the gate still stood. The landing page is indexed and the deck is not, because learners' uploaded notes become cards and a search engine's cache is the one thing a later commit cannot undo.
- **[#457](https://github.com/Kuantor/kuantorflow/issues/457): `/db/test` answered anyone**, and answered with the database username and host.
- **[#300](https://github.com/Kuantor/kuantorflow/issues/300): every static URL carries a version**, the file's modification time, so a deploy invalidates exactly what it changed. A cached stylesheet no longer renders a plausible wrong page.
- **[#462](https://github.com/Kuantor/kuantorflow/issues/462): the user guide described two of the six ceilings.** Now it describes all six, so Mykola, who answers from the guide, can explain a refusal.

After deploying, the live site was checked from outside: open without a keyword, the Help page rendering, the PDF current. Running the live smoke tests on PythonAnywhere exposed a gap: those tests could not run without the whole app installed. [kuantorflow_automation#172](https://github.com/Kuantor/kuantorflow_automation/pull/172) fixed that, and what remains is [kuantorflow_automation#173](https://github.com/Kuantor/kuantorflow_automation/issues/173).

### 2. Splitting app.py: 4,917 lines into six modules

[#418](https://github.com/Kuantor/kuantorflow/issues/418) took eight pull requests and ran in dependency order: `web.py` (the Flask object, configuration, identity and spending guards) ← `icons.py` ← `cards.py` ← `rounds.py` (the games and the quiz) ← `chat.py` (Mykola) ← `app.py`, which keeps sign-in, settings and account deletion and is 650 lines today. Nothing may import `app.py`, or the route table comes along with it.

It could only be done safely because of [#436](https://github.com/Kuantor/kuantorflow/issues/436), which went first. Every module now reaches shared names **through the module** (`web.is_admin()`), never through a copy bound at import. A bare copy still answers correctly; it is simply the one call site a test's stub can no longer reach, so the failure is silent in both directions. Before anything moved, [kuantorflow_automation#153](https://github.com/Kuantor/kuantorflow_automation/pull/153) proved that the suite stays offline, so a move that broke the stubbing would show up as a failure rather than as a real call to Oxford. A verification report ([kuantorflow_automation#162](https://github.com/Kuantor/kuantorflow_automation/pull/162)) records that all 38 routes survived the move and none of them behaves differently.

The rule that keeps the split from decaying is written in `CLAUDE.md`: **`web.py` takes only what two or more feature modules need.** Without it, `web.py` becomes `app.py` under another name, and every test stays green while that happens. The console one-off scripts moved to `scripts/` along the way (the first part of [#442](https://github.com/Kuantor/kuantorflow/issues/442)).

### 3. What a public site shows its visitors

- **[#460](https://github.com/Kuantor/kuantorflow/issues/460): a Help page**, linked in the header beside *About*. It renders `docs/user-guide.md`, **the same file Mykola indexes**, so the page and the companion cannot drift apart; the template holds structure and no help text. The Markdown is rendered when the page is requested, and the renderer is imported inside the route, so a deploy that misses `pip install` loses `/help` alone rather than the whole site. The downloadable PDF turned out to be a month stale and still told learners to type the keyword. It has been regenerated, and a test now fails when any heading in the guide is missing from it.
- **[#431](https://github.com/Kuantor/kuantorflow/issues/431): the Settings dialog pins *Cancel* and *Save*** in a footer, with the account actions moved out of it (closing [#120](https://github.com/Kuantor/kuantorflow/issues/120) and #465). A follow-up removed a horizontal scrollbar: a `<fieldset>` defaults to `min-width: min-content`, so one long unbreakable string widened the whole dialog.
- **[#453](https://github.com/Kuantor/kuantorflow/issues/453) and [#452](https://github.com/Kuantor/kuantorflow/issues/452): the topic page.** *Card deck* became a banner (the artwork converted to WebP), and on a phone the activity strip keeps only the back arrow.
- **[#448](https://github.com/Kuantor/kuantorflow/issues/448): logs that hold what they are named for.** `dict.log` had collected paid model calls and every spending refusal. Games and generated texts now have `games.log` and `gen_texts.log`, and every game round writes one `ROUND` line.
- **[#433](https://github.com/Kuantor/kuantorflow/issues/433): saving Settings refreshes the page** when the change affects what is rendered, so a hidden language no longer stays on screen.

### 4. A regression found on a phone

On 26 September the "Meet Mykola" welcome popup appeared squeezed on a phone, with its text in a column 28px wide. The cause was one character: removing a block for #431 had left its closing `}` behind. A stray brace at the top level of a stylesheet is not ignored. The browser reads everything up to the next `{` as a selector and **drops the following rule block** — here, the whole phone layout, which also held the lookup form's stacking. [#476](https://github.com/Kuantor/kuantorflow/pull/476) removed it, and [kuantorflow_automation#178](https://github.com/Kuantor/kuantorflow_automation/pull/178) now checks that the stylesheet's braces balance. The existing tests could not have seen it: several read rules straight out of `style.css`, and every rule was still in the file. Only a parser drops them.

### 5. Spaced repetition, redesigned before it is built

[#338](https://github.com/Kuantor/kuantorflow/issues/338), per-learner recall memory, was re-examined against how established systems store it: Anki's `revlog`, FSRS, and Duolingo's half-life regression data. Every mature system keeps an **append-only log of answers** as the source of truth and derives the schedule from it. When Anki moved to FSRS, it rescheduled everybody by replaying that history. So the design changed from a single state table to a log (phase 1) plus a schedule that can be rebuilt from it. Memory now attaches to **the word and part of speech** rather than the card, as Duolingo's does, which reverses the 18 September key decision. The work is filed as #338 (the log), [#479](https://github.com/Kuantor/kuantorflow/issues/479) (the SM-2 schedule), [#480](https://github.com/Kuantor/kuantorflow/issues/480) (the draw prefers due words) and [#92](https://github.com/Kuantor/kuantorflow/issues/92), rescoped to *Review (N due)*, with [#481](https://github.com/Kuantor/kuantorflow/issues/481) to check the constants against real answers later.

ai_agent had no pull requests this period. What Mykola knows about the app changed anyway, through the user guide he indexes, which lives in this repository.

---

## Technical Highlights

**Limits first, gate second.** Removing a control is safe only once whatever it was actually doing exists somewhere else. The keyword's real job was to cap the rate of paid calls, so six tickets built that cap before one ticket removed the keyword, and the order was written into #199 rather than left to memory.

**Patch where it lives.** #436 was a prerequisite with no user-visible effect, and it is the reason eight pull requests could move 4,000 lines without a single silent test. A refactor's risk is mostly in what tests *stop* reaching, not in what breaks.

**One file, two surfaces.** The Help page and Mykola read one declaration of where the guide is. The cheapest way to stop two copies drifting is to make sure there is only one copy.

**A log is cheaper than a guess.** The recall redesign replaced two open decisions ("record the spacing?" and "what counts as reliably right?") with a table that can answer either later. History cannot be backfilled, so the table that keeps it is the one that avoids early commitments.

---

## Lessons Learned

1. **A test that reads source cannot see what a parser throws away.** The stray brace left every rule in `style.css`, so every source-reading test passed. This is the fifth browser-only defect to ship green in this project; the check now counts braces, and the next step is proving layout against the rendered page.

2. **A helper that "never raises" can still raise at the call.** `applog._write(name, action, **fields)` swallows its own errors, but passing `action=` as a field raises a `TypeError` before its body runs. It shipped for one commit in #448 and silenced every refusal it touched. A field naming a feature is now written `feature=`.

3. **Committed build artefacts go stale quietly.** The guide's PDF was a month behind, and the first person to notice would have been a learner told to type a keyword that no longer exists. Anything generated and committed needs a test comparing it with its source.

4. **Proving a test fails only works if the break actually changed something.** Several deliberate breaks this fortnight were silent no-ops: a re-indentation that matched nothing, or a `git checkout` that staged the change so a plain diff showed nothing. `git diff HEAD --stat` before trusting a red run is now routine.

5. **Check what others do before settling a design question.** #338's key was decided on 18 September by reasoning alone. An hour with Anki's and Duolingo's published schemas reversed it within ten days, and that hour was cheap only because nothing had been built yet.

---

## Plans for Next Week

**#338 phase 1 first**: the answer log, one insert in `rounds._graded_answers()`, and a section in the user guide telling learners what is recorded, since the site is now public. #479's schedule follows once there are rows to replay. The calendar sets the pace more than the budget: SM-2's second interval is six days, so real scheduling cannot be observed any sooner.

### Highest Prio

Unchanged since the last edition. The fortnight went to #331's launch work, and nothing in this column was started.

| | |
|---|---|
| kuantorflow#100 | Quiz: treat perfective and imperfective answers as equal |
| kuantorflow#144 | Fill missing translations on cards parsed from notes |
| kuantorflow#185, #194 | Images for words and topics, and the copyright question they share |
| kuantorflow#216 | Descriptions for topic sections |
| kuantorflow#233 | Word games umbrella — **complete; should close** |
| ai_agent#19, #61 | Extend card meanings; message times in the chat |

### The SM-2 plan, in build order

| | |
|---|---|
| kuantorflow#338 | Phase 1: the log of graded answers, and the privacy note |
| kuantorflow#479 | The SM-2 schedule, derived from the log |
| kuantorflow#480 | The draw prefers due and never-seen words |
| kuantorflow#92 | *Review (N due)* |
| kuantorflow#481 | Later: check the constants against a month of answers |

### Worth doing, already specified

| | |
|---|---|
| kuantorflow#478 | The header banner needs a hover and focus state, because it is a link nobody can tell is one |
| kuantorflow#464 → #463 | Verify the automatic chat restart in a browser, then replace its slider with a number box |
| kuantorflow#430, #459 | Two tickets about which translation providers Settings offers. **They contradict each other and one needs to absorb the other.** |
| kuantorflow#90 | Tell learners their logs are stored. #338 covers its own table, not the logs. |

### Not on the board

| | |
|---|---|
| kuantorflow_automation#173 | The live smoke check still needs a kuantorflow checkout, not just its dependencies |

### Board housekeeping

*In Progress* holds [#227](https://github.com/Kuantor/kuantorflow/issues/227) and [#396](https://github.com/Kuantor/kuantorflow/issues/396), both closed long ago. [#233](https://github.com/Kuantor/kuantorflow/issues/233) and [#265](https://github.com/Kuantor/kuantorflow/issues/265) are umbrellas whose games are all built, and should close. [#331](https://github.com/Kuantor/kuantorflow/issues/331) has delivered the launch; what remains in it is Part 3.

---

*Report generated 27 September 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories, following the process recorded in `reports/README.md` (#252). This edition covers two weeks. Merged-PR window: 14 September 2026 (kuantorflow#434) through 26 September 2026 (kuantorflow#477), excluding the previous edition's own pull request (#432); kuantorflow_automation#152 through #179; no ai_agent pull requests.*
