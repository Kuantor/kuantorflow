# KuantorFlow: Future Enhancements

**For whoever runs the site, not for learners.** The Settings panel offers only the providers this deployment can use (kuantorflow#459). The ones it does not offer are still in the code: hidden, not deleted. This page says what each one is, and why it is not on.

Mykola does not read this page: his knowledge is `docs/user-guide.md` alone (`chat.MYKOLA_KNOWLEDGE`), so he never offers a learner a provider that cannot be picked.

---

## Translators

Every translator is declared once, in `parsers.TRANSLATORS`. **Claude** is on, using the same `ANTHROPIC_API_KEY` as Mykola. The other three are finished and tested, and each needs only its own key and account. Availability is read at the moment of a lookup, so adding the key to `.env` and reloading the web app is the whole change: no code, no schema step.

| Provider | Key in `.env` | What it changes |
|---|---|---|
| **Microsoft Translator** | `MS_TRANSLATOR_KEY` (and `MS_TRANSLATOR_REGION` for a regional Azure resource) | Groups its translations by part of speech, as Claude does, so one lookup can still make a noun card and a verb card |
| **DeepL** | `DEEPL_API_KEY` (the free and paid hosts are both tried) | Translates a string without grouping by part of speech: **one card per word** |
| **Google Cloud Translation** | `GOOGLE_TRANSLATE_API_KEY` | The same: **one card per word** |

**DeepL and Google Cloud would change the shape of the deck, not only who translates it.** A word that is both a noun and a verb gets one card instead of two. That is why the panel labels them *one card per word* when they are available.

**Two providers are retired, not hidden:** the scraped **Google** and **Bing** translators. They called endpoints nobody offered, and both stopped working on the same day (#348). They are gone from `TRANSLATORS` and cannot be chosen or stored (#353). Please don't propose them again; this paragraph is here so nobody has to rediscover why.

---

## Explanatory dictionaries

Declared in `parsers.DICTIONARIES`. **Oxford Learner's** (the default) and **Wiktionary** are on, and neither needs a key.

**Merriam-Webster** is the exception, and a key would not turn it on. `MERRIAM_WEBSTER_API_KEY` gates the choice, but the backend (`_merriam_webster_entry`) scrapes merriam-webster.com and sends no key anywhere. That website answers **403 to PythonAnywhere's addresses** (measured under #110, July 2026). So the enhancement is not "set the key". It is **#110: move to Merriam-Webster's official API**, with a choice of the *Learner's* or the *Collegiate* dictionary.

#110 has a comment (18 Oct 2026) summarising the API's terms:

- **non-commercial use only**, at most 1,000 queries a day per dictionary;
- **their logo** wherever their text is shown;
- **written approval** for automated queries, and probably for keeping their definitions on shared cards.

Ask them before building it.

---

## How to bring one back

1. Get the provider's key (and account), and put it in `kuantorflow/.env` on the server, never in code or chat.
2. Reload the web app. The provider appears in Settings, and learners can pick it.
3. To retire one for good, remove it from the declaration in a pull request that says why. Hiding needs nothing; it is what the panel already does with any provider that has no key.
