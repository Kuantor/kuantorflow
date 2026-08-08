# KuantorFlow — Weekly Development Report

**Period:** 2 – 8 August 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This period turned **topics from a text label into structure**, and then found out how bad the card content underneath had quietly been. Topics became a table, gained sections, gained an ordered curriculum, gained pictures — and the deck went from empty to **503 seeded cards live in production**. **41 pull requests merged** and the regression suite grew from **464 to 706 passing offline tests**, plus 67 opt-in database tests.

The week's most consequential work was not a feature. Seeding 360 words exposed a **parser bug that had been live since #21**: Oxford's single-sense entries were being skipped, so 143 of those 360 words came back with no English definition — and worse, some multi-sense words returned a *secondary* meaning while dropping the primary one. `hedge` was defined only as a financial instrument. It had gone unnoticed for months because a fallback masked it on developer machines and only production, where that fallback is IP-blocked, showed the truth.

| Repository | Role this period | Merged PRs |
|---|---|---|
| **kuantorflow** | Topics → table → sections → curriculum → pictures; three dictionary fixes; the main-page UI; logging | 20 |
| **kuantorflow_automation** | A parallel test PR for every change; suite → 706 offline | 20 |
| **ai_agent** | Mykola on Opus 5 with a cached system prompt | 1 |
| **Total** | | **41** |

**23 issues closed, all as completed** — no issue was closed as *not planned* this period. Two of them (#138, #161) turned out to be substantially satisfied by earlier work, and were closed with the evidence recorded rather than silently.

*Per the standing request, commit authorship was checked across all three repositories: all **89 commits** were authored by Kuantor, with Claude Opus 5 as co-author on the 21 feature commits. **No GitHub Copilot commits** were found in this range.*

---

## Completed Work by Theme

### 1. Topics became structure — five issues in dependency order

The arc of the week, each step needing the one before it:

- **A `topics` table** (#207, PR #209) — `flashcards.topic_id` references it, while `flashcards.topic` keeps the canonical spelling for the transition. A topic name becomes an id in exactly one place, so every producer upstream still speaks names.
- **Topic sections** (#215, PR #217) — `topic_sections` plus `topics.section_id` and `topics.position`. Two sections: `Other`, holding everything that predated the table, and `B2–C1 Conversational Topics`, created deliberately **empty**. Invisible on screen by design: every topic moved to position 0, which is the alphabetical order already displayed.
- **Sections on the page** (#218, PR #219) — headings with the empty curriculum shelf shown above `Other`, because a heading is *structure*: it says what the deck is going to be. Tiles became portrait to leave room for a picture.
- **The deck seeded** (#203, PR #220) — 18 B2–C1 topics × 20 words, built by the app's own lookup and save path. The word list is **content in version control**, and its order is load-bearing twice: it is the lookup order, so an interrupted run leaves the *useful* half, and it becomes each topic's position in the section.
- **Pictures on the tiles** (#223, PR #224) — a topic finds its icon by name, `static/img/topics/<slug>.webp`. A convention plus a directory listing: no column, no migration, nothing to keep in step with the table. Most topics have no icon, and that is the normal case — the existing flat tile *is* the fallback.

**#138** ("Develop Engaging Flashcard Topics"), open since June, was answered as part of this: six of its nine proposed categories were already covered, two were real gaps and were added (*Daily life and routines*, *Social interaction and small talk*), and its "Advanced Vocabulary" was declined on the ticket's own premise — the level lives in the words, not the headings.

### 2. Three dictionary bugs, found by using the thing at scale

- **Oxford dropped single-sense definitions** (#221, PR #222). The selector was `.sense > .def` — a *direct child*. Oxford wraps a definition in a `span.sensetop` whenever the sense carries extra furniture, which is always on a single-sense entry and often on the *first* sense of a multi-sense one. Measured over the 360 seeded words: **143 empty before, 9 after**. The nine remaining were lemma mismatches (`tactics` for `tactic`), swapped for the headword.
- **Lookups produced no example sentences** (#225, PR #226). `lookup_word()` never mentioned them; examples came only from Reverso, which production cannot reach. Oxford has them in markup already being downloaded — 97% of the seeded words have at least one — and they now arrive from the same single pass as the definitions.
- **Parts of speech did not match across providers** (#228, PR #230). Matching was exact string equality on a label one provider chose and the other did not, so `must` lost both ways: Google reports an *auxiliary verb*, Oxford a *modal verb*, and the definition was discarded **and** the card left blank. The synonym map was built from a 28-word survey of what each provider actually emits, and it also revealed that an Oxford entry can head *several* parts of speech (`both` is "determiner, pronoun"), which made those entries entirely unreachable.

### 3. Editing, moving, and the main page

- **Edit a saved card** (#176, PR #190) — every field including the word; ownership is part of the `UPDATE` rather than a check before it, and only the keys *present* in the submission are touched, which is what stops an editor that hides a language from wiping it.
- **Move a card to another topic** (#177, PR #192) — free text with existing topics as suggestions, and an unknown name creates the topic.
- **Duplicate prevention and individual-cards mode disagreed** (#186, PR #190) — "already in the database" could be said about a card the visitor cannot see; the message now names the setting responsible.
- **The main page redesigned** (#184, PRs #195/#196) — browsing leads, topics became tiles with room for a picture, the database check moved into Settings. Plus half-transparent panels (#197, PR #198) and a separate card opacity (#201, PR #202).

### 4. Guards, logging and process fixes

- **Notes upload refused without an account** (#200, PR #206) — it called Claude *before* the write guard, so an anonymous visitor could spend API budget on a card that would then be refused.
- **No chat logs for anonymous visitors** (#163, PR #193).
- **More verbose logs** (#161, PR #229) — a move now records **both** of its ends (the origin was being computed and thrown away), and settings changes are logged at all, separating what stuck from what the store rejected.
- **`md_to_pdf.py` could commit an error page as a report** (#211, PR #212) — headless Edge returns before rendering, so a successful exit said nothing. It now reads the finished PDF back.
- **`.obsidian/` and `.claude/` ignored** (#205, PR #204) — 248 lines of tool config one `git add -A` from being committed.
- **CLAUDE.md gaps** (#214, PR #213) — the test repository's real path, and that the database tests are skipped unless opted into.

### 5. Mykola

**Opus 5, with prompt caching** (ai_agent #63/#64, PR #66) — the model upgrade and a cached system prompt in one change.

### 6. Deployed to production

Two schema deploys ran cleanly (#207's topics table, #215's sections), and the deck was seeded on PythonAnywhere with `--owner`:

```
seeded / unowned / no explanation / no examples: (503, 0, 74, 86)
```

503 cards from 360 words, every one attributed. The 74 without an explanation are the accepted outcome from #228 — Google reports parts of speech Oxford does not have, and a translation is enough to keep a card. Notably the **production deck is cleaner than the local one** (14.7% without explanation against 22.4%), because production was seeded *after* the three dictionary fixes landed and the local deck was not.

---

## Technical Highlights

- **A fixture that shares the code's assumption cannot fail.** Every Oxford test fixture used the direct-child DOM shape — the same shape the buggy selector handled. The tests and the bug agreed, so the suite was green for months. The fix ships with fixtures for *both* shapes, taken from the real pages.
- **A fallback can hide the thing it falls back from.** Oxford's failures were invisible because Reverso answered instead, and Reverso answers from a laptop. The chain looked resilient and was actually concealing a defect that only production could show.
- **Move the seam, do not add a branch.** Fetching examples needed a richer Oxford call; reaching past the dispatch to get it meant a *stubbed* backend was bypassed, and three offline tests silently made live requests. They still passed. The clock gave it away — 1.2 seconds became 14.
- **Order can be load-bearing more than once.** `seed_words.py`'s order is the lookup order *and* each topic's position in its section, so one list serves resumability and presentation. Worth saying out loud, because a well-meaning `sorted()` would break both.
- **Declare the structure before filling it.** The seed places its eighteen topics into the curriculum section first and saves cards second. Reversed, the ordinary save path would have filed every one under `Other` at position 0 — correct for a topic a learner invents, wrong for a curriculum.
- **A convention can replace a column.** Topic icons are found by name from a directory listing. No migration, nothing to keep in sync — and when a topic owns an uploaded image (#185), this becomes the fallback without the template changing.
- **A card is worth keeping if it has a translation.** #228 was originally specified as "do not create a card the dictionary cannot explain". That was the wrong call for a bilingual app, was corrected on the ticket, and the acceptance criteria now carry an explicit guard: a diff that reduces the number of cards a lookup produces has misread it.

## Lessons Learned

1. **Measure before changing a number.** Two separate cases this week. The edit popup asks for 560px in its own CSS rule and computes to 420px, because a later rule with equal specificity overrides it — raising the number would have changed nothing. And the seed's word list looked like it needed 143 replacements; the parser needed one character.
2. **A green run on a developer machine is not evidence about production.** Reverso is reachable locally and blocked on PythonAnywhere, which is precisely why #221 survived. Anything whose behaviour differs between the two deserves a check that runs *there* — which is why the seed grew a `--check-oxford` command.
3. **A small sample under-estimates.** Fourteen words predicted that 10% of cards would carry translations only. Over all 360 the real figure is 14.7%. The prediction was the right shape and the wrong size, and it was quoted in a ticket before being corrected there.
4. **Ask what a log line is *for*.** A move recorded where a card landed and not where it came from, though the route had the origin in hand one line above. The field nobody had asked for was the only interesting one.
5. **Verification reports slipped.** Eight significant pull requests shipped this period with only **two** verification reports written (#176 and #207). The convention in `CLAUDE.md` exempts small PRs, not schema changes and parser rewrites. This is a real gap in the record and the most concrete process action for next week.

---

## Plans for Next Week

*Taken from the **KuantorFlow Improvements** GitHub Projects board ([dashboard](https://github.com/users/Kuantor/projects/2)), in board-column order.*

### Highest Prio

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#185](https://github.com/Kuantor/kuantorflow/issues/185) | Images for words and topics |
| kuantorflow | [#216](https://github.com/Kuantor/kuantorflow/issues/216) | Descriptions for topic sections |
| kuantorflow | [#194](https://github.com/Kuantor/kuantorflow/issues/194) | Copyright-safe image uploads |
| kuantorflow | [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective/imperfective answers as equal (AI) |
| ai_agent | [#61](https://github.com/Kuantor/ai_agent/issues/61) | Show the time of messages in the chat |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend card meanings |

**#185 and #216 are the natural next steps**, because both extend what shipped this week. #223 already resolves a topic's picture by name, so #185's per-topic uploaded image slots in as the *first* answer with the convention as fallback — no template change. And #216 gives sections the one thing they currently lack: a section is a heading with nothing to say about itself.

### In Progress — the word-games cluster

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#130](https://github.com/Kuantor/kuantorflow/issues/130) | Levenshtein multiple-choice translation game |
| kuantorflow | [#131](https://github.com/Kuantor/kuantorflow/issues/131) | Keyboard-adjacency typo distractors (MCQ) |
| kuantorflow | [#132](https://github.com/Kuantor/kuantorflow/issues/132) | "Is this a real word?" (n-gram pseudowords) |
| kuantorflow | [#133](https://github.com/Kuantor/kuantorflow/issues/133) | Typoglycemia scrambling mode |
| kuantorflow | [#110](https://github.com/Kuantor/kuantorflow/issues/110) | Investigation: Learner's vs Collegiate dictionary (+ M-W API) |

This cluster is now much better supplied than it was: a 503-card deck with definitions and example sentences is the raw material these games need, and #110's dictionary question is adjacent to the three Oxford fixes made this week.

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
| kuantorflow | [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards (local ↔ remote DB) |
| kuantorflow | [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme for the website |
| kuantorflow | [#75](https://github.com/Kuantor/kuantorflow/issues/75) | About popup: the agent's widget overlaps it |
| kuantorflow | [#76](https://github.com/Kuantor/kuantorflow/issues/76) | Scheduled recaps with random log selection |
| ai_agent | [#47](https://github.com/Kuantor/ai_agent/issues/47) | Audit the issue-61 changes |
| ai_agent | [#50](https://github.com/Kuantor/ai_agent/issues/50) | Make Mykola think faster |

### Human

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#147](https://github.com/Kuantor/kuantorflow/issues/147) | More accurate classification of words in "General" |

### Filed this week, not yet on the board

Four tickets came out of the week's work and are not yet in a board column:

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#227](https://github.com/Kuantor/kuantorflow/issues/227) | Widen the edit-card popup and give its text fields room |
| kuantorflow | [#231](https://github.com/Kuantor/kuantorflow/issues/231) | Oxford lookups miss words whose entries are numbered (`can`, `do`) |
| kuantorflow | [#191](https://github.com/Kuantor/kuantorflow/issues/191) | Edit dialog: a 'Look up & update' button that refills a card from the dictionaries |
| kuantorflow_automation | [#61](https://github.com/Kuantor/kuantorflow_automation/issues/61) | Bring the test catalogue back in step with the suite, and render it to PDF |

**#191 is the one with a standing cost.** Re-running the seed cannot repair an existing card — the duplicate rule skips it — so the 74 cards without an explanation and the 86 without examples stay as they are until #191 exists or the deck is rebuilt. Both #227 and #231 were investigated to the point of a diagnosis this week and deliberately left unimplemented.

---

*Report generated 8 August 2026 from GitHub pull-request, issue, project-board and commit data across the three repositories. Merged-PR window: 3 August 2026 (kuantorflow#190, kuantorflow_automation#44) through 8 August 2026 (kuantorflow#230, kuantorflow_automation#64); ai_agent#66 on 3 August.*
