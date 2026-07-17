# KuantorFlow — Weekly Development Report

**Period:** 14–17 July 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This week was the **Settings week**: the long-planned per-user settings platform
went from an open ticket to a fully shipped feature chain — storage layer,
Settings popup, applied behaviour, and real alternative providers — plus a
long-standing duplicate-cards bug fixed at the root. **10 pull requests merged**
across two repositories, and the regression suite grew from 42 to **72 passing
tests** (after first being repaired from a broken state).

What a user sees today that they didn't on Monday: a **Settings** item in the
header opens a popup where they can turn on *Add cards automatically*, choose
**Google or Bing** as the translator, and **Oxford Learner's or
Merriam-Webster** as the explanatory dictionary — each signed-in Google user
with their own persistent config file, anonymous visitors sharing a default
one. And looking up the same word twice no longer piles up duplicate cards:
the app says "Already in DB" instead.

| Repository | Role this week | Merged PRs |
|---|---|---|
| **kuantorflow** | Settings platform, providers, duplicate fix, SM-2 write-up | 5 |
| **kuantorflow_automation** | Suite repair, 30 new tests, gate helper, dedup script, deck tooling | 5 |
| **ai_agent** | No changes this week | 0 |
| **Total** | | **10** |

*Per the report request, commit authorship was checked across all repositories
for the period: every commit was authored by Kuantor (with Claude as
co-author); **no GitHub Copilot commits** were found in this time range.*

---

## Completed Work by Theme

### 1. The settings storage layer (kuantorflow #97 → issue #86)

The foundation everything else stands on: per-identity JSON config files in
`settings/` — `config-default.json` shared by anonymous visitors,
`config-<username>.json` per Google-authorised user (email prefix, sanitised
with the same rule as the per-user log directories).

- **Robust by design**: reads never raise (missing/corrupt files fall back to
  defaults), values are validated both ways (unknown keys dropped, bad values
  reset), writes are atomic (temp file + `os.replace`), and crafted emails
  can't escape the settings directory.
- **Self-provisioning** (#105): the first read for an identity now writes its
  config file with the defaults — nothing to provision on deploy. Corrupt
  files are never overwritten, so a hand-edited file stays inspectable.
- Adding a future setting = one entry in `settings_store.DEFAULTS`; existing
  files pick up the new default on their next read, no migration.

### 2. The Settings popup and applied behaviour (kuantorflow #105 → issues #13, #20)

A new **Settings** item in the header (between Home and About) opens a popup
dialog, pre-filled server-side from the identity's config and saved through a
validating `POST /settings` endpoint:

- **Add cards automatically** (#13, off by default) — when on, *Look up &
  save* writes parsed cards straight to the database with the green
  confirmation banner; when off, the review-before-save popup opens as
  before. (`.mht` uploads always go through review — editing the parsed
  lines is the point of that popup.)
- **Translation (ENG → UKR/RUS)** (#20) — Google Translate or Bing Translator.
- **Explanatory dictionary (ENG → ENG)** (#20) — Oxford Learner's
  Dictionaries (default) or Merriam-Webster.
- The lookup panel title follows the chosen translator, and the popup states
  whose settings are being edited (the signed-in email, or "all anonymous
  visitors").

### 3. Real providers behind the radio buttons (kuantorflow #106 → issue #21)

Lookups dispatch through `parsers.lookup_word(word, topic, translator,
explanatory_dictionary)` to one fetcher per provider — all real
implementations, verified live:

- **Bing Translator** — the week's most interesting engineering detour.
  bing.com's own web endpoints turned out to **reject non-browser TLS
  clients**: replaying a real browser session's cookies, token, and exact
  request body from Python still returned 401, while the identical request
  from inside the browser returned 200. The solution reaches the same
  Microsoft Translator engine the way the Edge browser's built-in translate
  does — a short-lived anonymous JWT from `edge.microsoft.com`, then the
  official `dictionary/lookup` API — returning byte-identical translations to
  the bing.com page, from plain `requests`.
- **Oxford Learner's Dictionaries** — one entry page covers one part of
  speech (`run_1` verb, `run_2` noun), so sibling entries linked from the
  page's *Other results* box are fetched too (capped at 3 pages, lookalikes
  like `run-up_1` ignored).
- **Merriam-Webster** — a single page carries every part-of-speech entry.
- **Graceful degradation throughout**: a translator that fails or returns
  nothing falls back to Google; a dictionary that fails falls back to
  Reverso; definition failures never break a lookup.

### 4. Duplicate cards fixed at the root (kuantorflow #107 → issue #101)

Looking up a word twice used to add it to the database twice. The check now
lives in `save_flashcard()` — the single write path shared by the review
popup, *Add All*, automatic add, and Mykola's chat saves — so every route is
covered at once:

- A card whose **word + part of speech** already exists anywhere in the
  database is skipped (word case-insensitive; the `pos` comparison NULL-safe,
  so pos-less `.mht` imports deduplicate too — something a database `UNIQUE`
  index could not do, since MySQL treats NULLs in a unique key as distinct).
- **The user is told**: the popup button turns into *"Already in DB"*, and
  the automatic-add banner counts skips — *"Added 1 card(s), skipped 1
  already in the database"*.
- A **maintenance script** (`maintenance/dedup_flashcards.py`, automation
  #10) cleans out duplicates accumulated before the fix: dry-run by default,
  `--apply` deletes, always keeping the oldest card of each group. To be run
  once against the live database after the next deploy.

### 5. Quality: suite repaired, then doubled (kuantorflow_automation #6, #8, #10)

- **Suite repair** (#6): the offline app tests had been silently broken —
  `authlib` missing from the requirements meant 35 tests couldn't even import
  the app, and 4 more had gone stale against the current UI. Fixed and green
  again at 42 tests.
- **30 new regression tests** (#8, #10) covering the whole week's work:
  config-file self-creation, corrupt files surviving untouched, `POST
  /settings` validation and per-identity persistence, popup markup and
  pre-fill, auto-add behaviour, provider dispatch with every fallback path,
  the Bing/Oxford/M-W parsers against canned responses, duplicate-skip logic
  against a fake database, and the dedup script's grouping rules.
- **Result: 72 passed**, fully offline, in under 2 seconds — plus an autouse
  fixture that redirects the settings store into a per-test temp directory so
  page renders can't write into the real checkout.

### 6. Test ergonomics and tooling (kuantorflow_automation #5, #9)

- **`gate.py`** (#9): one shared helper to pass the site's keyword gate in
  any scripted session — `enter_gate()` returns an authenticated session
  using `ACCESS_KEYWORD` from `.env`, with a clear error when the keyword is
  rejected. The live tests now use it, and it doubles as a CLI smoke check.
  A stale live test (favicon `icon.png` → `icon.jpg`) was fixed on the way,
  taking the live suite from 5/6 to 6/6.
- **Presentation tooling** (#5): a reusable python-pptx deck kit
  (`presentation/deck_kit.py`, KuantorFlow palette, 16:9) and a stdlib-only
  OOXML text extractor — future decks start from a library, not from scratch.

### 7. Documentation and idea work (kuantorflow #95)

- **Spaced-repetition (SM-2) proposal** (refs #94): a full write-up of a
  review-mode design for the flashcards, rendered to `reports/` in all three
  formats with the report tooling (whose `python-docx` dependency is now
  pinned).
- README grew sections for the settings store, the Settings popup, the four
  providers, and duplicate protection as each PR landed.

---

## Technical Highlights

- **TLS fingerprinting is the new bot wall.** The Bing investigation proved
  the block was neither cookies nor tokens (a full browser-session replay
  still 401'd) but the TLS client fingerprint itself — and found the
  legitimate alternative route that the Edge browser uses, which works from
  plain `requests`.
- **One write path, one check.** Because every card-saving flow funnels
  through `save_flashcard()`, the duplicate rule was implemented (and tested)
  exactly once — the popup, batch add, auto-add, and AI-chat saves all
  inherited it in the same commit.
- **Application-level uniqueness over a DB index — deliberately.** MySQL's
  unique indexes treat NULLs as distinct, so an index on `(word, pos)` would
  never catch repeated pos-less `.mht` imports; the NULL-safe `<=>` check
  covers them and needed no data migration.
- **Resolve dependencies at call time for testability.** Module-level dicts
  capturing fetcher functions at import made monkeypatching impossible in
  tests (and silently hit the real network); switching the provider dispatch
  to call-time resolution fixed both.
- **Stacked PRs kept reviews clean**: the #21 implementation PR was based on
  the #105 UI branch, so each diff stayed focused while merging in order
  retargeted everything automatically.

## Lessons Learned

1. **When replaying a request fails despite identical headers, cookies, and
   body — suspect the transport, not the payload.** Verifying the same
   request from inside the browser (200) versus Python (401) isolated TLS
   fingerprinting in minutes; hours could have been lost tweaking tokens.
2. **A test suite that can't import the app fails silently as "errors", not
   "failures"** — and can stay broken for days. The missing-`authlib` episode
   argues for running the suite (not just writing it) after every dependency
   change in the app repo.
3. **Fixtures that redirect filesystem side effects must arrive with the
   feature.** The moment the settings store learned to create files on first
   read, every page-rendering test began writing into the real checkout —
   caught only because the companion-test PR added the temp-dir fixture in
   the same change.
4. **HTML-escaping belongs in test expectations.** Assertions on flash
   messages with apostrophes must expect `&#39;` — the rendered page never
   contains the raw quote.
5. **Board hygiene pays off at report time**: several Highest-Prio tickets
   delivered this week (#86, #13, #20, #21) are still open on the board —
   closing tickets when their PRs merge keeps the dashboard truthful.

---

## Plans for Next Week

*Taken from the **KuantorFlow Improvements** GitHub Projects board
([dashboard](https://github.com/users/Kuantor/projects/2)). The board tracks
42 items as Done. Priority order below: Highest Prio first, then Nice To
Have, as requested.*

### Highest Prio

| Repo | Issue | Title | Note |
|---|---|---|---|
| kuantorflow | [#98](https://github.com/Kuantor/kuantorflow/issues/98) | Add "Reset Auth" button in Settings to clear Google login and keyword cache | **Next up** — extends the new Settings popup |
| kuantorflow | [#102](https://github.com/Kuantor/kuantorflow/issues/102) | [Bug] Prevent unauthenticated users from modifying default settings | **Next up** — hardening of the new `POST /settings` |
| kuantorflow | [#86](https://github.com/Kuantor/kuantorflow/issues/86) | Add config JSON files to store application settings | Delivered this week (PRs #97, #105) — ready to close |
| kuantorflow | [#13](https://github.com/Kuantor/kuantorflow/issues/13) | Settings Menu with Auto-Add Cards Option | Delivered this week (PR #105) — ready to close |
| kuantorflow | [#20](https://github.com/Kuantor/kuantorflow/issues/20) | UI: Add Dictionary Selection Feature in Settings | Delivered this week (PR #105) — ready to close |
| kuantorflow | [#21](https://github.com/Kuantor/kuantorflow/issues/21) | Implement Dictionary Selection Feature in Settings | Delivered this week (PR #106) — ready to close |

### Nice To Have

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#54](https://github.com/Kuantor/kuantorflow/issues/54) | Investigation: Android app clone of the KuantorFlow website |
| ai_agent | [#48](https://github.com/Kuantor/ai_agent/issues/48) | Let Mykola know that he is Claude-powered |
| kuantorflow | [#15](https://github.com/Kuantor/kuantorflow/issues/15) | Parallel PRs for features and tests |
| kuantorflow | [#87](https://github.com/Kuantor/kuantorflow/issues/87) | Add the user's Google avatar to the chat widget |
| kuantorflow | [#88](https://github.com/Kuantor/kuantorflow/issues/88) | Create a docx describing how to run backup/restore |
| kuantorflow | [#89](https://github.com/Kuantor/kuantorflow/issues/89) | Invisible field for the name of the user who added a card |
| kuantorflow | [#90](https://github.com/Kuantor/kuantorflow/issues/90) | Do we tell the user that the logs are stored on the server? |
| kuantorflow | [#92](https://github.com/Kuantor/kuantorflow/issues/92) | Spaced-repetition review mode |
| kuantorflow | [#93](https://github.com/Kuantor/kuantorflow/issues/93) | Complete multi-user card ownership |
| kuantorflow | [#99](https://github.com/Kuantor/kuantorflow/issues/99) | Show message about API credits if needed |
| kuantorflow | [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective and imperfective verb answers as equal using the AI agent |
| ai_agent | [#51](https://github.com/Kuantor/ai_agent/issues/51) | Support user-preferred nicknames in place of email login |
| kuantorflow | [#103](https://github.com/Kuantor/kuantorflow/issues/103) | Make Claude write test reports for each PR |
| kuantorflow | [#104](https://github.com/Kuantor/kuantorflow/issues/104) | Use the Allura font for the 'Welcome to KuantorFlow' message |

### In Progress

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#45](https://github.com/Kuantor/kuantorflow/issues/45) | UI: enhance behaviour of MHT-processing results window |
| kuantorflow | [#55](https://github.com/Kuantor/kuantorflow/issues/55) | Make config.json unique for each Gmail user |
| kuantorflow | [#30](https://github.com/Kuantor/kuantorflow/issues/30) | Create logs for various actions in the app |
| kuantorflow | [#46](https://github.com/Kuantor/kuantorflow/issues/46) | Settings: add language visibility switches |
| kuantorflow | [#84](https://github.com/Kuantor/kuantorflow/issues/84) | Darken the main page when the welcome popup is shown |
| kuantorflow | [#85](https://github.com/Kuantor/kuantorflow/issues/85) | Full-screen widget mode with visible maximize button |

### Todo

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign website for a modern look |
| kuantorflow | [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Dark theme for the website |
| kuantorflow | [#78](https://github.com/Kuantor/kuantorflow/issues/78) | Flashcards activity with Quizlet-style animation |
| kuantorflow | [#79](https://github.com/Kuantor/kuantorflow/issues/79) | Language disable checkboxes in Settings |
| kuantorflow | [#94](https://github.com/Kuantor/kuantorflow/issues/94) | Spaced-repetition review mode (SM-2) |
| kuantorflow | [#75](https://github.com/Kuantor/kuantorflow/issues/75) | About popup: agent's widget overlaps with it |
| kuantorflow | [#76](https://github.com/Kuantor/kuantorflow/issues/76) | Scheduled recaps with random log selection |
| kuantorflow | [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards (local ↔ remote database) |
| kuantorflow | [#19](https://github.com/Kuantor/kuantorflow/issues/19) | Tutorial tip above "Look Up & Save" button |
| kuantorflow | [#56](https://github.com/Kuantor/kuantorflow/issues/56) | Discussion: privacy disclosure & history bootstrap for per-user chat memory |
| kuantorflow | [#66](https://github.com/Kuantor/kuantorflow/issues/66) | Create a separate board for done items |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend the meanings of cards |
| ai_agent | [#47](https://github.com/Kuantor/ai_agent/issues/47) | Audit issue-61 changes: remove/refactor code not needed by the embedded widget |
| ai_agent | [#49](https://github.com/Kuantor/ai_agent/issues/49) | Runner setting for number of last logs |
| ai_agent | [#50](https://github.com/Kuantor/ai_agent/issues/50) | Make Mykola think faster? |
| kuantorflow_automation | [#4](https://github.com/Kuantor/kuantorflow_automation/issues/4) | Basic automated tests for the AI agent |

### Deployment follow-ups (carried on the tickets)

- Pull both repos on PythonAnywhere and reload (no new dependencies).
- Run `maintenance/dedup_flashcards.py` once against the live database
  (backup → dry run → `--apply`), per the note on
  [#101](https://github.com/Kuantor/kuantorflow/issues/101).
- Check that Bing/Oxford/Merriam-Webster are reachable from PythonAnywhere's
  IPs, per the note on
  [#21](https://github.com/Kuantor/kuantorflow/issues/21) — fallbacks keep
  lookups working either way.

---

*Report generated 17 July 2026 from GitHub PR, issue, and commit data across
the three repositories, and from the KuantorFlow Improvements project board.
Merged-PR window: 14 July 2026 (kuantorflow#95) through 17 July 2026
(kuantorflow#107, kuantorflow_automation#10).*
