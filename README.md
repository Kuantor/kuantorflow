# kuantorflow
Interactive language learning with notes and dictionaries

KuantorFlow is a flexible language‑learning platform built with Flask.  
It transforms notes, dictionary lookups, and study materials into interactive activities such as explanations, translations, flashcards, and quizzes.

---

## 🚀 Features
- Store words with **English explanations** and optional **Ukrainian/Russian translations**.
- Add **examples in multiple languages** to show real usage.
- Upload notes (e.g. `.mht` files from OneNote) and convert them into structured learning entries.
- Organize flashcards by topic for easy browsing.
- Practice with quizzes generated from your saved entries.
- Chat with **Mykola**, an AI study assistant, from a widget in the corner.
- **No duplicates** (#101): a card whose word + part of speech already exists
  anywhere in the database is never saved again — every save path (review
  popup, *Add All*, automatic add, Mykola chat) skips it and tells you the
  word is already present.

---

## 🤖 Mykola AI chat

A small chat widget sits in the bottom‑right corner of every page. It talks to
**Mykola**, the RAG (retrieval‑augmented) study assistant that lives in the
separate [`ai_agent`](https://github.com/Kuantor/ai_agent) repository.

**How the integration works — the agent code is imported, never duplicated:**

1. `app.py` adds the `ai_agent` checkout to `sys.path` (path from the
   `AI_AGENT_PATH` env var; defaults to a sibling folder `../ai_agent`) and
   imports `MykolaAgent` from it. If the repo or its dependencies aren't present,
   `MYKOLA_AVAILABLE` is `False` and the widget simply doesn't render.
2. The `/mykola/chat` route (behind the keyword gate, like everything else)
   receives `{question, history}`, calls `MykolaAgent.answer(...)`, and returns
   `{response, sources, history}` as JSON. All the RAG + Claude logic runs
   inside `ai_agent`; KuantorFlow only wires up the route and the UI.
3. The widget (in `templates/base.html`) posts to that route and renders
   Mykola's replies, including which knowledge‑base documents were used.

Because PythonAnywhere allows only one web app, Mykola cannot run as its own
Flask app in production — so its chat endpoint is served *by KuantorFlow*, but
the intelligence still comes straight from the `ai_agent` package.

**Deployment (PythonAnywhere):**

- Clone `ai_agent` next to `kuantorflow` (`/home/<user>/ai_agent`), so the
  default `AI_AGENT_PATH` finds it (or set `AI_AGENT_PATH` explicitly).
- Install its dependencies into KuantorFlow's virtualenv
  (`anthropic`, `scikit-learn`; already added to `requirements.txt`).
- Put `ANTHROPIC_API_KEY` in `ai_agent/.env` — Mykola reads its own key there,
  separate from KuantorFlow's `.env`.
- Reload the web app. With no key or credits, Mykola returns a friendly
  "out of tokens / set your key" message instead of a raw error.

---

## 👋 Welcome screen popup

KuantorFlow now shows a two-step welcome popup when the page loads:

1. Step 1 shows `static/img/main_image.jpg` and a `Next >` button in the
   bottom-right area.
2. Step 2 introduces Mykola with title/text and image (`mykola_poster.jpg` via
   `/mykola-media` when available; falls back to local avatar otherwise).
3. Pressing `Start Learning!` closes the popup and reveals the main app page.

Implementation details:

- Markup and behavior are in `templates/base.html`.
- Styling is in `static/css/style.css` under the "Welcome popup" section.
- No extra Flask route is required; it is template-driven and compatible with
  the existing layout and gate flow.

---

## ⚙️ Settings

User preferences live in JSON files under `settings/`, one per identity
(issue #86):

```
settings/config-default.json      every anonymous (not signed-in) visitor
settings/config-<username>.json   one per Google-authorised user
```

`<username>` is the part of the user's email before the `@`, sanitised to be
filesystem-safe — so signed-in users never share preferences. Anonymous
visitors share the default config, which is why it is **read-only for them**
(#102): one incognito visitor must not be able to change settings for every
other anonymous visitor. `POST /settings` requires a signed-in user (403
otherwise), and the popup renders with every control disabled plus a
sign-in hint.

`settings_store.py` is the whole storage layer, and `settings_store.DEFAULTS`
is the source of truth for which settings exist:

| Setting | Default | Introduced for |
| --- | --- | --- |
| `cards_automatically` | `false` | #13 — save looked-up cards without the review popup |
| `translator` | `google` | #20 — `google` or `bing` |
| `explanatory_dictionary` | `oxford` | #20 — `oxford` or `merriam-webster` |
| `show_ukrainian` | `true` | #46 — hide Ukrainian everywhere |
| `show_russian` | `true` | #46 — hide Russian everywhere |
| `quiz_lang` | `ukrainian` | #113 — the language the quiz opens in (`ukrainian` or `russian`) |

Behaviour worth knowing:

- **The files create themselves.** The first read for an identity writes its
  config file with the defaults, so nothing needs to be provisioned on
  deploy — the directory alone is enough. An unreadable or corrupt config
  falls back to the defaults rather than breaking the page (and is never
  silently overwritten).
- **Adding a setting** means adding one entry to `DEFAULTS`; existing config
  files pick up the new default on their next read, with no migration.
- **Values are validated** on read and write: unknown keys are dropped and
  out-of-range values fall back to their default.
- **Writes are atomic**, so a crash mid-write can't corrupt a config.
- The files are runtime state and are **gitignored**; `SETTINGS_DIR` can
  override the location.

In Python, `app.current_settings()` returns the active settings for the
request, and every template gets them as `settings`:

```python
if current_settings()["cards_automatically"]:
    ...
```

### The Settings popup (#13, #20)

**Settings** in the header opens a popup where each signed-in user edits
their own config file (saved via `POST /settings`, which runs through the
same validation as the store); anonymous visitors see the current defaults
read-only (#102):

- **Add cards automatically** (#13, off by default) — when on, *Look up &
  save* writes the parsed cards straight to the database and shows the usual
  green confirmation banner; when off, the review-before-save popup opens as
  before. `.mht` uploads always go through review, since editing the parsed
  lines is the point of that popup.
- **Translation (ENG → UKR/RUS)** (#20) — *Google Translate* or *Bing
  Translator*.
- **Explanatory dictionary (ENG → ENG)** (#20) — *Oxford Learner's
  Dictionaries* (default) or *Merriam-Webster*.
- **Visible translations** (#46/#79/#111) — *Show Ukrainian translation* and
  *Show Russian translation* checkboxes (both on by default). Unchecking one
  hides that language everywhere it appears: flashcards (translations and
  examples), the lookup review popup, the quiz (a hidden language can't be
  quizzed on; with both hidden the quiz explains itself), and Mykola's
  answers (he stops offering translations in it — though an explicit request
  in chat still wins). **Hiding is visual only**: lookups keep fetching and
  cards keep storing both languages, so re-enabling a language brings its
  translations back untouched.
- **Quiz language** (#113) — *Ukrainian* (default) or *Russian*: the language
  the quiz opens in. The in-page language switch still lets you take a quiz
  in any visible language — this only sets the default. The toggle is greyed
  out while only one language is visible (the quiz then uses that language
  automatically), and it re-enables live as the checkboxes above change.
- **Reset Auth** (#98) — an action button under the settings (an *action*,
  not a setting: it stays enabled for anonymous visitors despite the #102
  read-only freeze). After a confirmation dialog it clears the whole
  session — the gate pass and the Google sign-in — plus the app's own
  browser storage (chat-widget state, consent and welcome flags), landing
  back on the gate. Settings files are untouched: signing back in restores
  your preferences.

### The providers (#20, #21)

Word lookups go through `parsers.lookup_word(word, topic, translator,
explanatory_dictionary)`, which dispatches to one fetcher per provider
(`_translator_backend()` / `_dictionary_backend()`):

- **Google Translate** — the public `translate.googleapis.com` JSON endpoint
  (`dt=bd` returns dictionary entries grouped by part of speech).
- **Bing Translator** — the same Microsoft Translator engine that powers
  bing.com/translator, reached the way the Edge browser's built-in translate
  feature does: a short-lived anonymous token from `edge.microsoft.com`,
  then the official `dictionary/lookup` API. (bing.com's own web endpoints
  reject non-browser TLS clients, so they can't be used from a server.)
- **Oxford Learner's Dictionaries** — scraped entry pages; one page covers
  one part of speech, so sibling entries (`run_1`, `run_2`, …) linked from
  the page's *Other results* box are fetched too (up to 3 pages).
- **Merriam-Webster** — scraped dictionary page; a single page carries every
  part-of-speech entry.

The dispatch degrades gracefully: a translator backend that fails or returns
nothing falls back to Google Translate, and a dictionary backend that fails
or returns nothing falls back to Reverso's dictionary — a lookup without
definitions is still useful, so definition failures never break a lookup.

---

## 📂 Project Structure

```
kuantorflow/
├── app.py              # Main Flask application (routes, views)
├── utils.py            # Database connection + helper functions
├── settings_store.py   # Per-user settings persisted as JSON (issue #86)
├── requirements.txt    # Python dependencies
├── README.md           # Project documentation
├── .gitignore          # Ignore secrets, venv, cache files
├── templates/          # HTML templates (index.html, flashcards.html, quiz.html)
├── static/             # CSS, JS, images
├── settings/           # Per-identity config JSON (gitignored, created at runtime)
└── uploads/            # Uploaded MHT files (optional)
```
