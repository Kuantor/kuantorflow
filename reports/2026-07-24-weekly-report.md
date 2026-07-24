# KuantorFlow — Weekly Development Report

**Period:** 18–24 July 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

This week finished and hardened the **Settings platform**, grew **Mykola** into a more capable companion, and added genuinely new **study and content** features — a flip-card deck, a Reverso copy-paste parser with AI-assisted translation splitting, and an early duplicate-word warning. **19 pull requests merged** across the three repositories, and the regression suite grew to **114 passing offline tests** — every feature now shipped with a parallel test PR and, for the significant ones, a committed test report.

| Repository | Role this week | Merged PRs |
|---|---|---|
| **kuantorflow** | Settings hardening, study/content features, UI polish | 11 |
| **kuantorflow_automation** | Test PRs, the test-report convention, suite → 114 | 7 |
| **ai_agent** | Mykola identity | 1 |
| **Total** | | **19** |

*Per the standing request, commit authorship was checked across all three repositories for the period: every commit was authored by Kuantor (Claude as co-author). **No GitHub Copilot commits** were found in this range.*

---

## Completed Work by Theme

### 1. Settings platform — finished and hardened

The remaining Highest-Prio settings items all landed:

- **Reset Auth** (#98, PR #117) — a button that clears the whole session (keyword gate + Google identity, both held in the signed cookie) and the app's browser storage, returning to the gate. Enabled even inside the read-only popup, since it's an action, not a setting.
- **Anonymous settings frozen** (#102, PR #116) — anonymous visitors share `config-default.json`, so one of them must not change it for everyone: `POST /settings` now refuses them (403) and the popup renders read-only with a sign-in prompt.
- **Two-column popup layout** (#118, PR #119) — the popup had grown past the viewport; the fieldsets now sit in a responsive two-column grid, the actions share one row, and the dialog clamps to the screen.
- **Mobile widget hidden with the popup** (#121, PR #122) — on phones the Mykola widget no longer overlaps the open Settings popup.

### 2. Mykola the companion

- **"New Chat" button** (ai_agent#55, PR #128) — a small pencil in the widget header starts a fresh conversation behind a Yes/No confirmation, re-running the welcome-back recap for signed-in users; past exchanges stay logged.
- **Claude-powered identity** (ai_agent#48, PR #53) — Mykola now answers "Are you Claude?" honestly and in character, while still giving his playful symbolic age.

### 3. Richer study & content

- **Card deck activity** (#78, PR #135) — a Quizlet-style flip-card deck per topic: one card at a time, click/Enter/Space to flip, arrow keys to move.
- **Reverso copy-paste parser** (#134, PR #141) — `.mht` notes that are OneNote copy-pastes of Reverso entries are auto-detected and parsed into rich cards (one per word + part of speech, senses aggregated, with examples). Reverso glues the translation terms together with no separators, so **Claude splits them back apart**, keeping multi-word phrases intact, with a graceful fallback when no API key is present.
- **Early duplicate-word warning** (#145, PR #149) — looking up a word that already has cards now warns *before* the lookup and review dialog ("look it up anyway?"), complementing the save-time duplicate skip from #101.

### 4. UI polish & docs

- **Blue links panel** (#142, PR #143) — the "Home | Card deck | Take quiz" menu on the flashcards, deck and quiz pages is now a blue rounded panel.
- **Live website link in the README** (#115, PR #123; PR #124) — with access instructions.

### 5. Process & quality (kuantorflow_automation)

- **Test-report convention** (#103, PR #18) — significant PRs now get a Markdown + PDF verification report committed under `test_reports/`, rendered with the report tooling; small PRs are exempt unless requested.
- **Seven test PRs** covered every feature above; the offline suite grew from ~100 to **114 passed**, still under three seconds and fully offline.
- The **parallel feature-and-test PR** practice (#15) was confirmed and recorded.

---

## Technical Highlights

- **Format detection as a state machine.** The Reverso parser recognises the copy-paste format by its colour-coded markup and walks the paragraphs as a header → sense → explanation/example/translation state machine — robust to the fixed OneNote export shape.
- **AI where the markup can't help.** Reverso concatenates translation terms with no separators, so they cannot be split from the HTML; a single batched Claude (haiku) call splits them, keeping phrases like "верховный правитель" whole — and degrading to the whole string when offline.
- **Graceful degradation throughout.** The translation split, and the duplicate-word check, both treat a missing API key or an unreachable DB as "unknown" and proceed, so no feature hard-fails in a degraded environment.
- **One escaping bug worth noting.** Threading parsed examples through the review popup exposed that Jinja's `tojson` output isn't attribute-safe on its own (its quotes break `value="…"`); `tojson | forceescape` fixes it.
- **Session-only auth model paid off.** Because the gate pass and Google identity both live in the signed session cookie, Reset Auth is a one-line `session.clear()` — no tokens to track.

## Lessons Learned

1. **`tojson` needs `forceescape` inside an HTML attribute.** Its output is HTML-safe for text nodes but its literal `"` characters break a double-quoted attribute value.
2. **Design for the degraded path first.** Both the AI translation split and the duplicate warning were built to work with no API key / no database, which also made them trivial to test offline.
3. **Confirm destructive-looking actions, but keep them cheap.** New Chat and Reset Auth both got Yes/No dialogs; neither is actually destructive (chats stay logged, settings files survive), which is worth stating in the copy so users aren't scared off.
4. **The project board isn't machine-readable without the scope.** Reading the "KuantorFlow Improvements" board needs the GitHub `project` token scope, which the current credential lacks — so this week's plan below is grouped from the open issues by theme rather than by board column.

---

## Plans for Next Week

*Grouped from the open issues across the three repositories. The precise
"Highest Prio / Nice To Have" board columns could not be read this time (the
credential lacks the GitHub `project` scope), so the grouping below is by
theme.*

### Content & note import

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#137](https://github.com/Kuantor/kuantorflow/issues/137) | Upload notes: support .txt and .docx besides .mht |
| kuantorflow | [#144](https://github.com/Kuantor/kuantorflow/issues/144) | Add Ukrainian/Russian translations after parsing MHT files |
| kuantorflow | [#136](https://github.com/Kuantor/kuantorflow/issues/136) | Images on flashcards (files on disk, path in MySQL) |
| kuantorflow | [#147](https://github.com/Kuantor/kuantorflow/issues/147) | More accurate classification of words in the "General" topic |
| kuantorflow | [#140](https://github.com/Kuantor/kuantorflow/issues/140) | [Optional] Upload notes: support .pdf (text-layer) |

### New study modes & word games

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#94](https://github.com/Kuantor/kuantorflow/issues/94) / [#92](https://github.com/Kuantor/kuantorflow/issues/92) | Spaced-repetition review mode (SM-2) |
| kuantorflow | [#127](https://github.com/Kuantor/kuantorflow/issues/127) | Create "Individual" cards mode |
| kuantorflow | [#129](https://github.com/Kuantor/kuantorflow/issues/129) | Smart word-manipulation techniques for exercises (spike) |
| kuantorflow | [#130](https://github.com/Kuantor/kuantorflow/issues/130) / [#131](https://github.com/Kuantor/kuantorflow/issues/131) | Levenshtein / keyboard-adjacency MCQ distractors |
| kuantorflow | [#132](https://github.com/Kuantor/kuantorflow/issues/132) / [#133](https://github.com/Kuantor/kuantorflow/issues/133) | "Is this a real word?" & typoglycemia modes |
| kuantorflow | [#100](https://github.com/Kuantor/kuantorflow/issues/100) | Quiz: treat perfective/imperfective answers as equal (AI) |

### Multi-user, admin & security

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#125](https://github.com/Kuantor/kuantorflow/issues/125) | Deny unauthorized users to add/change/delete words |
| kuantorflow | [#148](https://github.com/Kuantor/kuantorflow/issues/148) | IDs for users |
| kuantorflow | [#93](https://github.com/Kuantor/kuantorflow/issues/93) | Complete multi-user card ownership |
| kuantorflow | [#126](https://github.com/Kuantor/kuantorflow/issues/126) / [#139](https://github.com/Kuantor/kuantorflow/issues/139) | Blacklist file / delete-request to admin |

### Mykola (ai_agent)

| Repo | Issue | Title |
|---|---|---|
| ai_agent | [#57](https://github.com/Kuantor/ai_agent/issues/57) | Confirmation popup for Mykola adding cards |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement/extend card meanings |
| ai_agent | [#56](https://github.com/Kuantor/ai_agent/issues/56) | Make Mykola's avatar bigger |
| ai_agent | [#58](https://github.com/Kuantor/ai_agent/issues/58) | Save the conversation to a file |

### UI polish & investigations

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#146](https://github.com/Kuantor/kuantorflow/issues/146) | Common "Cancel" cross for closing the new-card popup |
| kuantorflow | [#120](https://github.com/Kuantor/kuantorflow/issues/120) | Pin the Settings close button while the dialog scrolls |
| kuantorflow | [#22](https://github.com/Kuantor/kuantorflow/issues/22) / [#74](https://github.com/Kuantor/kuantorflow/issues/74) | Website redesign / dark theme |
| kuantorflow | [#54](https://github.com/Kuantor/kuantorflow/issues/54) | Android app (investigation — repo, board & sub-tasks created) |
| kuantorflow | [#110](https://github.com/Kuantor/kuantorflow/issues/110) | Merriam-Webster free API (blocked-on-PA workaround) |

---

*Report generated 24 July 2026 from GitHub PR, issue and commit data across the three repositories. Merged-PR window: 18 July 2026 (kuantorflow#116/#117) through 24 July 2026 (kuantorflow#149, kuantorflow_automation#21).*
