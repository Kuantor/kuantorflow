# KuantorFlow — Weekly Development Report

**Period:** 9–13 July 2026 · **Repositories:** [kuantorflow](https://github.com/Kuantor/kuantorflow), [ai_agent](https://github.com/Kuantor/ai_agent), [kuantorflow_automation](https://github.com/Kuantor/kuantorflow_automation)

---

## Executive Summary

In five days, KuantorFlow grew from an empty repository into a deployed, AI-powered
language-learning platform: **65 merged pull requests** across three repositories,
live on PythonAnywhere.

The product today: a Flask web application where Ukrainian- and Russian-speaking
learners build bilingual flashcard collections from dictionary lookups and OneNote
exports, drill them with quizzes — accompanied by **Mykola**, a persona-rich AI study
companion (Claude-powered, RAG-grounded) who answers questions, adds flashcards to
the database on request, remembers signed-in users between visits, and greets them
with a recap of their previous conversations.

| Repository | Role | Merged PRs |
|---|---|---|
| **kuantorflow** | The website: flashcards, quizzes, auth, Mykola integration | 35 |
| **ai_agent** | The agent: RAG core, persona, tools, standalone demo app | 28 |
| **kuantorflow_automation** | Quality & ops: regression tests, DB backups | 2 |
| **Total** | | **65** |

---

## Completed Work by Theme

### 1. Product foundation (kuantorflow #1–#12)

The core learning product was built in the first two days:

- **Flashcards end-to-end** (#1): parsing word lookups and OneNote `.mht` exports
  into cards, saving to MySQL, browsing by topic, and a bilingual (UA/RU) quiz mode.
- **Smarter lookups** (#2, #7): one card per part of speech, English definitions,
  translation gap-filling between Ukrainian and Russian; the lookup backend was
  switched from Reverso scraping to Google Translate's dictionary data for
  reliability.
- **Review before save** (#12): looked-up cards go through an edit/delete review
  popup instead of saving blind — a pattern later reused for file imports.
- **Polish and access control**: blue/yellow theme with topic chips (#3), card
  deletion with confirmation (#9), Open Graph link previews (#8), favicon (#10),
  environment-based DB credentials (#5), and a keyword access gate for the whole
  site (#11).

### 2. Birth of the agent (ai_agent #1–#9)

- **RAG core** (#1): a retrieval-augmented agent answering English-learning
  questions from a markdown knowledge base (TF-IDF retrieval over heading-split
  chunks) with source citations.
- **Standalone web app** (#2): Flask chat UI with gallery/About pages.
- **The keystone architecture decision** (#9): the chatbot was extracted into an
  importable `TynnaAgent` class so the main site could *import* the agent rather
  than duplicate it — the single-source, two-repo composition that every later
  feature builds on.
- Early capabilities: flashcards-database access and study-list generation
  (#15, #16), friendly API-error messaging (#5), chat-output markdown fixes
  (#6, #18), a music player for the standalone app (#13, #14).

### 3. Integration era (kuantorflow #26–#39)

The agent moved into the main site as a floating chat widget:

- **Widget integration** (#26) with minimize/maximize, avatar animation, and an
  About page — served by a **cross-repo Jinja loader** that renders the shared
  About partial and media straight from the ai_agent checkout (no copies).
- **Two-step welcome popup** introducing the companion (#33, #37), wave-animation
  launcher (#39), full-page chat opened from the widget (#35), and chat logging
  (#32; ai_agent #24).

### 4. Platform hardening

- **The dependency saga** (kuantorflow #28; ai_agent #21, #22): a `lxml` build
  failure on Python 3.14 (no `cp314` wheel for the pinned version) was diagnosed
  and fixed; all dependencies were pinned, a fully-resolved `requirements.lock`
  introduced for reproducible deploys, and both repos aligned to the same
  production versions. Production was migrated to Python 3.13 on PythonAnywhere.
- **Request-size caps** (kuantorflow #29; ai_agent #22): 1 MB guards on chat
  endpoints against memory abuse and runaway API costs — scoped per-endpoint so
  legitimate large `.mht` uploads still work.
- **Log privacy** (kuantorflow #57; ai_agent #42): gitignore hardening so
  per-user chat logs can never be accidentally committed.

### 5. Identity & personalization (kuantorflow #41, #43; ai_agent #29)

- **Optional "Sign in with Google"** (OAuth 2.0 / OpenID Connect via Authlib) with
  an accept-or-decline consent popup — anonymous use remains fully supported, and
  the identity lives only in the session cookie, never in the database.
- Sign-in persists **30 days** across browser restarts; Mykola greets by full name
  and addresses the user by **first name** in conversation (with prompt-injection
  hardening on the name).

### 6. The Mykola transformation (ai_agent #27, #32, #34, #36, #41; kuantorflow #47)

The agent's persona evolved from Tynna to **Mykola** — a distinguished gentleman
named in honour of composer Mykola Leontovych ("Shchedryk" / "Carol of the Bells"):

- Full cross-repo rename: code, routes, templates, CSS, assets, log directories.
- **A voice guide, not just a bio** (#36): British lexicon and courtesy patterns,
  few-shot example exchanges, moderation rules (one flourish per reply, clarity
  first), plus three new knowledge-base documents (French–English connections,
  British English, music-based learning) so the persona's signature moves are
  *retrieved and cited*, not improvised.
- Symbolic birthday with dynamic age (#41), copyright-aware musical style
  (titles, themes, short fragments — never full lyrics).

### 7. Agent capabilities: acting, remembering, staying current

- **Tool use** (ai_agent #37; kuantorflow #48): a real `add_flashcard` tool —
  when asked, Mykola fills all nine card fields himself (POS, definition,
  UA + RU translations and examples, topic) and saves through the same
  `save_flashcard` mechanism as the site's own flows, injected by kuantorflow
  (dependency direction preserved).
- **Per-user memory** (kuantorflow #52; ai_agent #38): signed-in users' chats are
  logged to `mykola_logs/<email_prefix>/`; on return, Mykola opens with a recap
  of previous conversations plus suggested follow-ups.
- **Focused context** (kuantorflow #59; ai_agent #44): recaps read only the
  **3 newest logs** (12k-char secondary budget), lead with the most recent topic —
  and if the learner already said goodbye today, Mykola deterministically wishes
  them a good rest instead of restarting the dialogue.
- **Readable logs** (kuantorflow #58; ai_agent #43): log files named by chat
  start time (`chat_2026-07-13_11-43-03_e6f3.txt`).
- **Live UI updates** (kuantorflow #60): when Mykola saves a card mid-chat, the
  "Browse flashcards" topic chips refresh in place — no reload.
- **Widget-state persistence** (kuantorflow #64; ai_agent #45, #46): the chat
  survives internal page navigation, keeping its history and open/maximized state.

### 8. Content workflow & visual identity

- **MHT two-column review** (kuantorflow #44): file uploads no longer auto-save —
  a popup shows the source text beside editable parsed cards with per-card
  delete and an "Add All" button.
- **Imagery refresh** (kuantorflow #50, #51): new icon, main artwork, and
  backgrounds for the Mykola era.

### 9. Quality & operations (kuantorflow_automation #1, #3)

- **pytest suite** (#1): app regression tests plus live-site smoke tests.
- **Daily database backups** (#3): scheduled dump script for the production MySQL.

---

## Technical Highlights

- **Two-repo composition without duplication.** The site imports the agent class,
  renders the agent repo's templates through a `ChoiceLoader`, and serves its media
  through dedicated routes — one source of truth for code, prose, and assets.
- **Feature-detected rollouts.** Cross-repo integrations probe capabilities at
  runtime (`inspect.signature`, `hasattr`) so the two repositories can merge and
  deploy in any order without breaking each other.
- **Dependency injection at the seam.** kuantorflow injects its own `save_flashcard`
  into the agent's tool executor — the agent stays storage-agnostic; the site keeps
  one write path for all card sources.
- **LLM engineering in production**: TF-IDF RAG with citations, a streaming
  tool-use loop, persona/voice prompt engineering with few-shot examples, and
  defense-in-depth against prompt injection (names reduced to a single token,
  tool inputs sanitized to a whitelist).
- **Deterministic where determinism wins.** Farewell detection, date checks, and
  the goodnight reply are plain string/date logic — no model call, fully testable.
- **Security throughout**: parameterized SQL everywhere, XSS-safe DOM building for
  user-controlled strings, OAuth CSRF handled by Authlib, per-endpoint request
  caps, secrets in `.env` only, and consent copy kept honest as storage semantics
  changed.

## Lessons Learned

1. **Wheel availability is the real Python-version constraint.** A "Visual C++
   required" error almost always means a pinned package predates your interpreter
   (`cpXYZ` in the wheel name tells the story) — bump the pin, don't install a
   compiler. The same lesson set production's Python 3.13 floor via numpy/scipy.
2. **Same-name asset swaps fight browser caches.** Refreshing `background.jpg` and
   the favicon in place required hard refreshes; version query-strings are the
   durable fix.
3. **Keep disclosure in sync with behavior.** When per-user chat storage arrived,
   the consent popup's "nothing is stored on our server" had to change the same
   day — honesty is a feature requirement, not an afterthought.
4. **Verify with stubs, prove with one live call.** The test pattern that worked:
   exhaustive deterministic matrices with stubbed agents (free, fast), then a
   single real end-to-end call as the final proof — including cleanup of any real
   rows it created.
5. **Squash merges hide branch ancestry.** Post-merge cleanup needs `git branch -D`
   plus PR-state verification rather than trusting `-d`'s merged check.
6. **gitignore patterns don't recurse the way you hope.** `dir/chat_*.txt` does not
   cover `dir/sub/chat_*.txt` — user-identified logs nearly became committable the
   day subdirectories appeared.

---

## Plans for Next Week

*Derived from open GitHub issues across all repositories (excluding this report's
own ticket, kuantorflow#63). The GitHub Projects board statuses were not readable
with current token scopes, so status is mapped from assignment: assigned items are
listed as In Progress, unassigned as Todo.*

### In Progress

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#55](https://github.com/Kuantor/kuantorflow/issues/55) | Make config.json unique for each Gmail user |
| kuantorflow | [#54](https://github.com/Kuantor/kuantorflow/issues/54) | Investigation: Android app clone of the KuantorFlow website |
| kuantorflow | [#46](https://github.com/Kuantor/kuantorflow/issues/46) | Settings: add language visibility switches |
| kuantorflow | [#45](https://github.com/Kuantor/kuantorflow/issues/45) | UI: enhance behaviour of MHT-processing results window |
| kuantorflow | [#22](https://github.com/Kuantor/kuantorflow/issues/22) | Redesign website for a modern look |
| kuantorflow | [#13](https://github.com/Kuantor/kuantorflow/issues/13) | Settings menu with auto-add cards option |
| ai_agent | [#19](https://github.com/Kuantor/ai_agent/issues/19) | Teach Mykola to complement and extend the meanings of cards |

### Todo

| Repo | Issue | Title |
|---|---|---|
| kuantorflow | [#62](https://github.com/Kuantor/kuantorflow/issues/62) | Create DOCX tutorial documenting KuantorFlow development from scratch |
| kuantorflow | [#56](https://github.com/Kuantor/kuantorflow/issues/56) | Discussion: privacy disclosure & history bootstrap for per-user chat memory |
| kuantorflow | [#30](https://github.com/Kuantor/kuantorflow/issues/30) | Create logs for various actions in the app |
| kuantorflow | [#25](https://github.com/Kuantor/kuantorflow/issues/25) | Export/import cards (local ↔ remote database) |
| kuantorflow | [#21](https://github.com/Kuantor/kuantorflow/issues/21) / [#20](https://github.com/Kuantor/kuantorflow/issues/20) | Dictionary selection feature in Settings |
| kuantorflow | [#19](https://github.com/Kuantor/kuantorflow/issues/19) | Tutorial tip above "Look Up & Save" button |
| kuantorflow | [#15](https://github.com/Kuantor/kuantorflow/issues/15) | Parallel PRs for features and tests |
| ai_agent | [#47](https://github.com/Kuantor/ai_agent/issues/47) | Audit issue-61 changes: remove/refactor code not needed by the embedded widget |
| ai_agent | [#28](https://github.com/Kuantor/ai_agent/issues/28) | UI: sync Mykola's description on both screens |
| kuantorflow_automation | [#4](https://github.com/Kuantor/kuantorflow_automation/issues/4) | Basic automated tests for the AI agent |

---

*Report generated 13 July 2026 from GitHub PR and issue data across the three
repositories (`gh pr list` / `gh issue list`), covering every merged PR from the
first (kuantorflow#1, 9 July 2026) to the most recent (kuantorflow#64, 13 July 2026).*
