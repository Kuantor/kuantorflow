# KuantorFlow — Spaced Repetition (SM-2)

**Implementation and usage report** · 28 September 2026 · Umbrella ticket [#487](https://github.com/Kuantor/kuantorflow/issues/487)

---

## Summary

KuantorFlow now remembers how each learner does with each word and brings words back just before they would be forgotten. The site **records** every answer the games check, **schedules** each word with the SM-2 algorithm, **prefers** due words in every game, and offers a **Review (N due)** button that starts a round of exactly the words due today. A short-term companion brings *Fill the gap*'s missed words back in the very next round.

Six tickets were built, all merged on 28 September 2026:

| Ticket | What it delivers | Code PR | Test PR |
|---|---|---|---|
| [#338](https://github.com/Kuantor/kuantorflow/issues/338) | The answer log, and the privacy section of the user guide | [#483](https://github.com/Kuantor/kuantorflow/pull/483) | [automation#180](https://github.com/Kuantor/kuantorflow_automation/pull/180) |
| [#484](https://github.com/Kuantor/kuantorflow/issues/484) | *Fill the gap* ticks recorded, weighed as the learner's own verdict | [#485](https://github.com/Kuantor/kuantorflow/pull/485) | [automation#181](https://github.com/Kuantor/kuantorflow_automation/pull/181) |
| [#479](https://github.com/Kuantor/kuantorflow/issues/479) | The SM-2 schedule, calculated from the log | [#486](https://github.com/Kuantor/kuantorflow/pull/486) | [automation#182](https://github.com/Kuantor/kuantorflow_automation/pull/182) |
| [#480](https://github.com/Kuantor/kuantorflow/issues/480) | Every one-card game deals due and never-seen words first | [#488](https://github.com/Kuantor/kuantorflow/pull/488) | [automation#183](https://github.com/Kuantor/kuantorflow_automation/pull/183) |
| [#92](https://github.com/Kuantor/kuantorflow/issues/92) | *Review (N due)*: a round of the words due today | [#489](https://github.com/Kuantor/kuantorflow/pull/489) | [automation#184](https://github.com/Kuantor/kuantorflow_automation/pull/184) |
| [#337](https://github.com/Kuantor/kuantorflow/issues/337) | *Fill the gap*: missed words come back next round | [#490](https://github.com/Kuantor/kuantorflow/pull/490) | [automation#185](https://github.com/Kuantor/kuantorflow_automation/pull/185) |

**Still open:** [#481](https://github.com/Kuantor/kuantorflow/issues/481), a report that checks the schedule's settings against about a month of real answers. It needs data, so it can't usefully start before late October.

---

## Using it: the learner's view

**1. Sign in and play.** Only a signed-in learner is recorded. Seven games count toward the schedule: the **Quiz**, **Multiple choice**, **Scrambled**, **Spell it**, **Rebuild the sentence**, **Listen and type**, and **Fill the gap**. *Odd one out* and *Real or fake* don't, because their questions aren't about one card.

**2. Words come back on their own.** Each word gets a date on which it is next worth asking. In any of those seven games, words due today and words never answered are dealt first; a word already known for now comes up several times less often, but is never left out.

**3. Review (N due).** When words are due, a yellow **Review (N due)** button appears at the top of *Practise your words* on the front page. It opens a page listing the due words, longest-waiting first, and the games to review them in. A review is an ordinary round of the chosen game, dealt only from the due words, and when it's over, **Back to review** shows what is still due.

**4. Fill the gap.** Here the learner marks themselves by ticking *I remember it*. **Finish** records every card turned over. An unticked card counts as forgotten; a tick counts, but for less than an answer the site checked. The cards left unticked come back in the next round (up to half of it), within the same sitting and whether or not the learner is signed in.

**5. Privacy.** The record is private to the learner, and **deleting the account erases it**. The user guide says so under *What the site remembers about your answers*, and it's also what `/help` and Mykola read.

---

## How it works

The design keeps **a log and a schedule derived from it**. That's how Anki (its `revlog`) and FSRS work, and research into them settled the design on 27 September:

| Layer | Where | Role |
|---|---|---|
| **The log** | `recall_answers` | One row per answer, appended and never changed. The source of truth. |
| **The schedule** | `recall_schedule` + `recall.py` | Each word's next due date: a **cache** of the log, rebuildable at any time. |
| **The draw** | `games.sample()` + `rounds._draw_weight()` | Deals due and never-seen words first. |
| **The review** | `/review` + `?review=1` | A round dealt only from due words. |
| **The sitting** | the browser session | *Fill the gap*'s missed words, for the next round only. |

**Why a log rather than a table of counters.** The log can't lose history and has no race to design around: two rounds finishing at once simply add two sets of rows. More importantly, the schedule can always be recalculated from it. Change a rule in `recall.py`, run `scripts/rebuild_schedule.py`, and every learner's schedule becomes what the new rule would always have produced. That's how Anki moved every user to FSRS.

**Why memory attaches to the word, not the card.** The deck is shared, holds duplicate cards, and cards get edited or deleted. Keying on **word and part of speech** (Duolingo's "lexeme") means duplicates count as one word, and a deleted card's word keeps its history. This reversed an earlier decision (18 September) after the research.

**Why only checked answers drive the schedule.** Six games compare the learner's answer with the card, which is stronger evidence than a self-rating. *Fill the gap*'s ticks are recorded too, but flagged as the learner's own verdict (`Activity.self_marked`) and weighed lower.

---

## The rules

**SM-2 on right/wrong answers:**

- **A pass** gives 1 day, then 6, then `round(interval × ease)`.
- **A fail** resets to 1 day, counts a lapse, and lowers the ease by 0.2.
- **Ease** starts at 2.5 and never goes below 1.3. A pass leaves it unchanged, so with right/wrong answers it can only fall. That's conservative on purpose: easy words get reviewed a little too often rather than any being forgotten.

| Successful review | ease 2.5 (never missed) | ease 2.3 (missed once) |
|---|---|---|
| 1st | 1 day | 1 day |
| 2nd | 6 days | 6 days |
| 3rd | 15 | 14 |
| 4th | 38 | 32 |
| 5th | 95 | 74 |

**Four rules added for this app:**

1. **Only the first answer of a learner's day moves the schedule.** Three rounds in one afternoon would otherwise push a word from 1 to 6 to 15 days, the opposite of spacing.
2. **The day is Kyiv time and starts at 04:00**, so a late-night session stays in the day it began. This is why `tzdata` is a requirement.
3. **A correct answer before the word is due is practice** and moves nothing. A wrong one still counts as a lapse. Without this, a word played four days in a row would reach 37 days without its spacing ever being tested.
4. **A *Fill the gap* tick is weaker evidence.** On a day with any checked answer, the checked one decides. An unticked card is a full lapse. A tick gives half the growth, never more than 6 days on its own.

**The draw's weights:** due, overdue or never answered → **1**; scheduled for later → **0.2**. In a review round, each day overdue adds 1, so the longest-waiting words come first. Over 300 real rounds on the local deck, never-answered words were dealt 136 times each and not-yet-due words 29 times each.

---

## The data

| Table | Key | Contents |
|---|---|---|
| `recall_answers` | `id` | `user_id`, `card_id` (the card shown), `word` and `pos` (copied), `game`, `correct`, `answered_at` (UTC, one instant per round) |
| `recall_schedule` | `user_id, word, pos` | `reps`, `lapses`, `ease` (thousandths: 2500 = 2.5), `interval_days`, `due_on` (a date) |

**Deleting an account erases both tables' rows** for that account (CASCADE). **Deleting a card keeps its answers**, with `card_id` set to empty, because what the learner knew doesn't stop being true.

---

## Operations

**Deploying from scratch**, the steps the six PRs added:

```bash
venv/bin/pip install -r requirements.txt
```

```bash
venv/bin/python scripts/apply_schema.py --dry-run
venv/bin/python scripts/apply_schema.py
```

Reload the web app, then:

```bash
venv/bin/python scripts/rebuild_schedule.py --dry-run
venv/bin/python scripts/rebuild_schedule.py
```

**Run the rebuild again after any change to the rules in `recall.py`.** A second run always says `nothing to do`.

**Looking at the data:**

```bash
venv/bin/python -c "from utils import get_db_connection; c=get_db_connection(); u=c.cursor(); u.execute('SELECT word, pos, reps, lapses, ease, interval_days, due_on FROM recall_schedule ORDER BY due_on, word'); [print(*map(str, r), sep='  |  ') for r in u.fetchall()]"
```

**Testing on the same day, locally only.** Setting `due_on` to today makes words due, and the rebuild script restores the real schedule afterwards. The exact commands are on [#92](https://github.com/Kuantor/kuantorflow/issues/92#issuecomment-5877586313) and [#480](https://github.com/Kuantor/kuantorflow/issues/480#issuecomment-5874457744).

---

## Verification

**Hand-tested on real data.** Rounds were played on the local database and on PythonAnywhere, and each confirmed the rows written. Four of the six checked games were confirmed by hand across the two, and the rest by the automated tests. The rebuild's dry run, real run and `nothing to do` re-run were checked on the local learner's data. The live update and the rebuild agreed on all ten words.

**Automated tests.** Every PR has a parallel test PR, and every test file was **proved to fail** by breaking the committed code on purpose:

| Ticket | Breaks | Caught |
|---|---|---|
| #338 | 9 | 9 |
| #484 | 6 | 6 |
| #479 | 16 | 16 (one after a second version of the break) |
| #480 | 12 | 12 (one after the test was tightened) |
| #92 | 12 | 12 |
| #337 | 11 | 11 |

The suite grew from 2,273 offline tests (27 September) to **2,392**, with a database tier of real-MySQL tests for the SQL.

**Bugs caught before release:**

- ***Listen and type* dropped each word's part of speech**, so every scheduled verb there looked never answered. Caught by the draw test before any deliberate break.
- ***Fill the gap* could swap a missed verb for its noun**, losing the repeat. Found by playing the round in a browser, then fixed and covered by a test.
- **Three tests were too weak to fail**, and were rewritten before merging: a shuffle test, a cap test and a timing guard.

**Browser checks.**

- ***Fill the gap*'s *Finish*** sends only the cards turned over.
- ***Play again*** waits for the save: 1.2 s with the save slowed to one second, and 1.5 s with it blocked.
- **The review page** has no sideways scrolling at desktop or phone width.

---

## What's next

**[#481](https://github.com/Kuantor/kuantorflow/issues/481)**, after about a month of answers, will check whether:

- **the intervals hold** (recall at each interval stays high);
- **typed answers deserve more weight** than multiple-choice ones;
- **the 0.2 weight** for words not yet due is right.

Because the schedule is a cache of the log, any change it recommends is an edit to `recall.py` followed by one rebuild.

**Optional:** a permanent *Review* link for days when nothing is due. Today the review page can only be reached from the button, or by typing its address.

---

*Report prepared 28 September 2026 from the merged pull requests, the tickets' comments and the verification runs recorded in them.*
