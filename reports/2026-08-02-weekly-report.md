# KuantorFlow — Weekly Development Report

**Period:** 25 July – 2 August 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This period delivered the **users and ownership cluster end to end** — from "the app has no idea who you are" to signed-in identities, per-card ownership, admin powers, account deletion, blocking, and a private-cards mode. **42 pull requests merged** across the three repositories and the regression suite grew from **114 to 464 passing offline tests**, a fourfold increase driven by the parallel-test convention.

It also included the project's **first production incident**, on 2 August: card saving returned errors for real users because a deploy silently skipped a schema change. The fix was not only the missing column but the deploy step itself, which is now a re-runnable, self-reporting command.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | The users/ownership cluster, action logs, notes formats, the schema deploy step | 19 |
| **kuantorflow_automation** | A parallel test PR for every feature, 13 verification reports, suite → 464 | 20 |
| **ai_agent** | Away-aware recaps, and Mykola's second tool | 3 |
| **Total** | | **42** |

**24 issues closed** — 19 completed, four closed as *not planned* after being specified and thought through (soft delete #159, the Recycle Bin #160, delete-requests-to-admin #139, and the Allura welcome font #104, which was implemented and then declined on the look), and one as a duplicate. The reasoning was recorded on each rather than the issue being left open indefinitely.

*Per the standing request, commit authorship was checked across all three repositories for the period: all 90 commits were authored by Kuantor (Claude as co-author). **No GitHub Copilot commits** were found in this range.*

---

## Completed Work by Theme

### 1. Identity and ownership — the cluster, complete

The site went from anonymous-only to a real multi-user model, in dependency order:

- **A `users` table** (#148, PR #166) — keyed on Google's OIDC `sub`, not the email. A user who changes their Gmail address keeps the same row and the same cards, instead of appearing as a second person.
- **Every card records who added it** (#89, PR #169) — `added_by_user_id`, taken from the server-side session only, never from posted form data.
- **Delete only your own cards** (#162, PR #173) — enforced in the route as one conditional `DELETE`; the admin may delete any.
- **An admin identity** (#158, PR #172) — `ADMIN_EMAILS` in the server `.env`, requiring Google's `email_verified` claim. Configuration rather than a database column, deliberately: the admin's job is to block accounts, so admin-ness must not live in the table the blocking flow edits.
- **Delete my account** (#165, PR #179) — self-service, with an explicit keep-or-delete choice for the user's cards, plus an admin command-line script for accounts that cannot remove themselves.
- **Only an account may change the database** (#125, PR #182) — anonymous visitors can read everything and write nothing; every save path refuses in the route, not by hiding a button.
- **Blocking an account** (#126, PR #183) — a blocked learner keeps reading but cannot write or use Mykola, and is shown the admin's address so they can ask for access back.
- **Individual cards mode** (#127, PR #187) — an opt-in setting that hides everyone else's cards from the topic list, deck and quiz.
- **#93, the multi-user umbrella**, was re-read against what had shipped and **closed as delivered** by the four issues above.

Two supporting fixes landed alongside: per-user settings and chat logs were re-keyed on the user id rather than the email prefix (#174, PR #175), and the chat widget stopped replaying a previous user's conversation after a sign-out (#170, PR #171).

### 2. The deploy step that skipped schema changes

On 2 August, saving a card began returning errors in production. The cause was not the code that shipped but the **deploy instructions**: `schema.sql` creates tables with `CREATE TABLE IF NOT EXISTS`, which does nothing to a table that already exists, and the `ALTER` statements that would add a new column lived in that file only as **comments**. Re-applying the file reported success either way.

**#180 (PR #181)** replaced it with `apply_schema.py`: one command that creates missing tables *and* applies pending column changes, skips whatever is already present, prints every step it applied and every one it skipped, and exits non-zero on failure. The migrations are now real statements in an ordered list rather than prose. Its first production run was a clean no-op, which was itself the proof that the idempotency works — and #126's two new columns were the first migration written under the new scheme.

### 3. Mykola

- **Away-aware recaps** (ai_agent#54, PRs #60 and kuantorflow#156) — after a configurable break, Mykola reviews the last exchanges and opens a fresh conversation acknowledging how long the learner was away.
- **Anonymous message limits** (#164, PR #168) — a per-session nudge and a per-day ceiling that actually bounds the API bill, counted atomically so concurrent workers cannot both slip past the last message.
- **Mykola remembers what to call you** (ai_agent#62, PRs ai_agent#65 + kuantorflow#188) — a second agent tool. Saying *"Anna Maria is a mouthful, call me Ann"* stores the preference and he uses it from the next message on; *"go back to my proper name"* clears it. The tool is defined in the agent, but the callable that touches the database is injected by KuantorFlow, so the agent still never talks to MySQL.

### 4. Content and logging (earlier in the period)

- **Notes uploads accept `.txt` and `.docx`** as well as `.mht` (#137, PR #154) — one parser per format feeding a shared state machine.
- **Action logs** (#30, PR #155) — a plain-text, greppable trail of cards created, skipped, deleted and refused; which dictionaries were used and how long they took; and every notes upload. Logging never breaks a user action.
- **One close cross for the review popup** (#146, PR #153) and the `.mht` results-window behaviour (#45).

### 5. Process

- **`CLAUDE.md` in all three repositories** (#152, PRs #151/#59/#22) — the guidance a new contributor or agent needs: architecture, conventions, and the traps.
- **13 verification reports** committed this period, one per significant change, each recording what was checked and how.
- The suite reached **464 offline tests** running in about six seconds, plus opt-in round-trip tests against a real local database.

---

## Technical Highlights

- **Key on what cannot change.** The `users` table is keyed on Google's `sub` claim rather than the email, because an email is user-editable and a key must not be. The same reasoning made `blocked_at` a nullable timestamp rather than a boolean: it answers *is this blocked* and *since when* in one column.
- **Enforce in the route, not the template.** Greying a control is presentation, and a hand-made request goes straight past it. Every rule this period — delete-your-own, sign-in-to-write, blocked accounts — is enforced where the request is handled, with the UI merely reflecting it.
- **One funnel, so a rule cannot be forgotten.** All card writes already went through a single function; the anonymous-write rule was enforced *there*, so a future save path that forgets to ask fails loudly instead of quietly writing.
- **`NULL` is not a value.** `added_by_user_id = 5` never matches an unowned card, which is exactly right for "only my cards" — and exactly wrong if "no filter" is expressed as an owner of `None`, which would hide the entire deck. The individual-cards filter is built around that distinction.
- **Read live what must be current.** A block is read from the database on each signed-in request rather than stamped into the session, because a session cookie lasts thirty days and a block must take effect on the next request.
- **Tools the model can reach for.** Mykola's second tool follows the first one's shape exactly: the agent defines it and returns a status the model relays in character, while the host injects the callable that touches the database — and refuses by raising, which becomes an honest in-character apology rather than a crash.

## Lessons Learned

1. **A comment is not a deploy step.** The outage was possible because a required `ALTER` lived inside a file the deploy already ran, so following the instructions faithfully still skipped it. Anything a deploy must do belongs in something that *runs*, reports what it did, and fails loudly.
2. **A stored shape that gains a field needs a decision about the old shape.** This bit three times in one session — a session dict gaining an id, widget state gaining an owner, a session gaining a verification claim. In each case the old shape read as a *valid* new one rather than as missing, so nothing failed loudly.
3. **Tightening a rule invalidates the tests that codified the old freedom.** Denying anonymous writes broke fifteen tests; most needed only a fixture swap, but four had to be rewritten because their subject — a card saved with no owner — is behaviour that no longer exists. That distinction is worth budgeting for.
4. **Verify the thing that cannot be unit-tested.** Whether the model actually *reaches for* a new tool is not something a stubbed test can answer, so the preferred-name feature was checked against the live model in four real conversations, including both refusal paths.
5. **An audit trail should count events, not attempts.** Unblocking an account twice initially wrote two log lines though the second changed nothing — found while verifying, not while testing.

---

## Plans for Next Week

*Taken from the **KuantorFlow Improvements** GitHub Projects board ([dashboard](https://github.com/users/Kuantor/projects/2)), in board-column order.*

### Highest Prio

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#176](https://github.com/Kuantor/kuantorflow/issues/176) | Edit a saved card: every field, including the word |
| kuantorflow | [#184](https://github.com/Kuantor/kuantorflow/issues/184) | Create new UI page |
| kuantorflow | [#185](https://github.com/Kuantor/kuantorflow/issues/185) | Images for words and topics |
| kuantorflow | [#163](https://github.com/Kuantor/kuantorflow/issues/163) | Do not write chat logs for anonymous visitors |
| kuantorflow | [#144](https://github.com/Kuantor/kuantorflow/issues/144) | Add Ukrainian/Russian translations after parsing MHT files |
| kuantorflow | [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective/imperfective answers as equal (AI) |
| kuantorflow | [#99](https://github.com/Kuantor/kuantorflow/issues/99) | Show a message about API credits when needed |
| ai_agent | [#63](https://github.com/Kuantor/ai_agent/issues/63) | Move Mykola from Claude Opus 4.8 to Opus 5 |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend card meanings |
| ai_agent | [#61](https://github.com/Kuantor/ai_agent/issues/61) | Show message times in the chat |

**#176 is the natural next step**: it gives the action log's edit entry its first call site, and it is the last unbuilt half of #125 — there is currently no edit route for the "change" rule to protect.

### Nice To Have

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#177](https://github.com/Kuantor/kuantorflow/issues/177) | Move a card to a different topic |
| kuantorflow | [#178](https://github.com/Kuantor/kuantorflow/issues/178) | Rename a topic (bulk) — admin-only |
| kuantorflow | [#136](https://github.com/Kuantor/kuantorflow/issues/136) | Images on flashcards (files on disk, path in MySQL) |
| kuantorflow | [#92](https://github.com/Kuantor/kuantorflow/issues/92) / [#94](https://github.com/Kuantor/kuantorflow/issues/94) | Spaced-repetition review mode (SM-2) |
| kuantorflow | [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign website for a modern look |
| kuantorflow | [#161](https://github.com/Kuantor/kuantorflow/issues/161) | Make logs more verbose |
| kuantorflow | [#85](https://github.com/Kuantor/kuantorflow/issues/85) / [#88](https://github.com/Kuantor/kuantorflow/issues/88) / [#90](https://github.com/Kuantor/kuantorflow/issues/90) | Full-screen widget / backup docs / logs disclosure |
| ai_agent | [#64](https://github.com/Kuantor/ai_agent/issues/64) | Cache Mykola's system prompt (prompt caching) |
| ai_agent | [#56](https://github.com/Kuantor/ai_agent/issues/56) / [#57](https://github.com/Kuantor/ai_agent/issues/57) / [#58](https://github.com/Kuantor/ai_agent/issues/58) | Bigger avatar / add-card confirmation / save conversation |
| kuantorflow_automation | [#4](https://github.com/Kuantor/kuantorflow_automation/issues/4) | Automated tests for the Mykola AI agent |

*ai_agent [#51](https://github.com/Kuantor/ai_agent/issues/51) (user-preferred nicknames) is now largely delivered by #62 and worth re-reading before any work starts on it.*

### In Progress — the word-games cluster

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#130](https://github.com/Kuantor/kuantorflow/issues/130) | Levenshtein multiple-choice translation game |
| kuantorflow | [#131](https://github.com/Kuantor/kuantorflow/issues/131) | Keyboard-adjacency typo distractors (MCQ) |
| kuantorflow | [#132](https://github.com/Kuantor/kuantorflow/issues/132) | "Is this a real word?" (n-gram pseudowords) |
| kuantorflow | [#133](https://github.com/Kuantor/kuantorflow/issues/133) | Typoglycemia scrambling mode |
| kuantorflow | [#110](https://github.com/Kuantor/kuantorflow/issues/110) | Investigation: Learner's vs Collegiate dictionary (+ M-W API) |

### Todo (selected)

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#186](https://github.com/Kuantor/kuantorflow/issues/186) | Duplicate prevention and individual-cards mode disagree about what exists |
| kuantorflow | [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme for the website |
| kuantorflow | [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards (local ↔ remote DB) |
| kuantorflow | [#120](https://github.com/Kuantor/kuantorflow/issues/120) | Pin the Settings close button while the dialog scrolls |
| kuantorflow | [#140](https://github.com/Kuantor/kuantorflow/issues/140) | [Optional] Upload notes: support .pdf (text-layer) |
| kuantorflow_automation | [#14](https://github.com/Kuantor/kuantorflow_automation/issues/14) | Refresh the test catalog — it lists 94 tests against 464 today |

### Outstanding operational item

**#125, #126, #127 and ai_agent#62 are merged but not yet confirmed deployed.** The deploy needs a `git pull` of *both* `kuantorflow` and `ai_agent`, then `apply_schema.py` — which is **not** a no-op this time, as it adds #126's two columns — followed by a web-app reload.

---

*Report generated 2 August 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories. Merged-PR window: 25 July 2026 (kuantorflow#151) through 2 August 2026 (kuantorflow#188, kuantorflow_automation#43, ai_agent#65).*
