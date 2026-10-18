"""
Either aspect of a verb is right in the Quiz (#100).

A Ukrainian or Russian verb has two aspects -- *писати* / *написати*,
*складывать* / *сложить* -- and a card stores whichever one the translator
gave. So a learner who typed the other aspect of a stored verb was marked
wrong, on about one card in five (the verbs).

**A rule cannot find the partner.** Many pairs change the stem, not a prefix
(*відшкодовувати* / *відшкодувати*, *давати* / *дати*), and a prefix often
changes the meaning (*писати* "to write", *описати* "to describe"). So the
pairs are **read in advance** from Wiktionary, which names a verb's partner
in its headword template (`{{uk-verb|писа́ти|impf|pf=написа́ти}}`), and stored
in `aspect_pairs`. Grading then only looks them up: no wait, no paid call and
no failure mid-quiz. Only the *fact* that two forms are a pair is stored, never
Wiktionary's text (#258's line: existence, not content).

    aspects.fill({"ukr": [...], "rus": [...]}, source="script")
    aspects.fill_for_card(card)        # a new verb card, after it is saved

`scripts/fill_aspect_pairs.py` runs `fill()` over the deck; a card saved during
an outage is picked up by the next run. Measured on 30 Sept 2026 over the local
deck: Wiktionary names the pair for 83% of Russian and 55% of Ukrainian forms.
"""

import re
import time

import requests

import applog
import parsers
import utils

ACUTE, GRAVE = chr(0x301), chr(0x300)

# The headword templates that name a verb's partner, per quiz language.
TEMPLATES = {"ukr": "uk-verb", "rus": "ru-verb"}
TEMPLATE = re.compile(r"\{\{(uk|ru)-verb\|([^{}]*)\}\}")
PARTNER_KEY = re.compile(r"^(pf|impf)\d*$")

# Wikimedia's rate limit: about one request a second already drew HTTP 429
# when #100 was measured; batches of 25 titles with a 6-second pause did not.
BATCH = 25
PAUSE_SECONDS = 6
RETRIES = 3


def normalise(form):
    """A form as it is stored and compared: lower case, stress marks gone,
    ё folded onto е (the quiz's own rule, #267), spaces trimmed."""
    text = (form or "").strip().lower().replace(ACUTE, "").replace(GRAVE, "")
    return " ".join(text.replace("ё", "е").split())


def is_verb(card):
    """A verb card: its part of speech says verb, and not adverb."""
    pos = (card.get("pos") or "").lower()
    return "verb" in pos and "adverb" not in pos


def single_word_variants(translation):
    """A stored translation's variants that are one word. Phrases (*брати в
    оренду*) are out of scope: their pair is the leading verb's, a later step."""
    return [v.strip() for v in (translation or "").split(",")
            if v.strip() and " " not in v.strip()]


def partners_in_wikitext(text, lang):
    """The partners a page's headword templates name for `lang`: a set, empty
    for a verb of both aspects; None when the page has no such template."""
    wanted = TEMPLATES[lang].split("-")[0]
    found, seen = set(), False
    for code, params in TEMPLATE.findall(text or ""):
        if code != wanted:
            continue
        seen = True
        for param in params.split("|"):
            key, eq, value = param.partition("=")
            if eq and PARTNER_KEY.match(key.strip()):
                # One value can name several, comma-separated (завчити's
                # `impf=завча́ти,заучува́ти`, found by the first real fill).
                for name in value.replace("[[", "").replace("]]", "").split(","):
                    partner = normalise(name)
                    if partner:
                        found.add(partner)
    return found if seen else None


def fetch_partners(forms, lang, get=None, sleep=None):
    """Ask Wiktionary for each form's partners: `{normalised form: set}`.

    A page that is missing, or has no verb template for `lang`, is "checked,
    no partner" (an empty set). A batch that **failed** is left out entirely,
    so it is asked again next time rather than stored as having no partner.
    Polite to Wikimedia: batches of `BATCH`, a pause between them, and a 429's
    `Retry-After` honoured.
    """
    get = get or requests.get
    sleep = sleep or time.sleep
    wanted = list(dict.fromkeys(f.strip().lower().replace(ACUTE, "") for f in forms if f.strip()))
    found = {}
    for start in range(0, len(wanted), BATCH):
        if start:
            sleep(PAUSE_SECONDS)
        chunk = wanted[start:start + BATCH]
        pages = _query(chunk, get, sleep)
        if pages is None:
            continue
        for title in chunk:
            text = pages.get(title)
            partners = partners_in_wikitext(text, lang) if text else None
            found[normalise(title)] = partners or set()
    return found


def _query(titles, get, sleep):
    """{asked title: wikitext or ""} for one batch, or None when it failed."""
    params = {"action": "query", "prop": "revisions", "rvprop": "content",
              "rvslots": "main", "titles": "|".join(titles), "redirects": 1,
              "format": "json", "formatversion": "2"}
    for _attempt in range(RETRIES):
        try:
            resp = get(parsers.WIKTIONARY_API, params=params,
                       headers=parsers.WIKTIONARY_HEADERS, timeout=20)
            if resp.status_code == 429:
                sleep(int(resp.headers.get("Retry-After") or PAUSE_SECONDS))
                continue
            resp.raise_for_status()
            query = resp.json().get("query") or {}
        except Exception:
            return None
        # A redirect (еще -> ещё) answers under its target's title; map it back.
        back = {}
        for hop in (query.get("normalized") or []) + (query.get("redirects") or []):
            back[hop.get("to")] = back.get(hop.get("from"), hop.get("from"))
        out = {}
        for page in query.get("pages") or []:
            title = page.get("title")
            asked = back.get(title, title)
            try:
                text = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                text = ""
            out[(asked or "").lower()] = text
        return out
    return None


def fill(forms_by_lang, source):
    """Check every form not yet checked, and store what Wiktionary says.

    `forms_by_lang` is `{"ukr": [...], "rus": [...]}` of single-word variants
    as stored. Returns `{lang: {"asked": n, "paired": n}}`, and writes one
    `ASPECT-PAIRS` line per language that asked anything.
    """
    summary = {}
    for lang, forms in forms_by_lang.items():
        # Asked by its title form -- lower case, stress marks gone, but ё kept,
        # since Wiktionary's titles keep it -- and stored normalised.
        by_norm = {}
        for form in forms:
            title = form.strip().lower().replace(ACUTE, "").replace(GRAVE, "")
            by_norm.setdefault(normalise(form), title)
        checked = utils.aspect_forms_checked(lang, set(by_norm))
        todo = [by_norm[n] for n in by_norm if n not in checked]
        if not todo:
            summary[lang] = {"asked": 0, "paired": 0}
            continue
        found = fetch_partners(todo, lang)
        if found:
            utils.save_aspect_pairs(lang, found)
        paired = sum(1 for partners in found.values() if partners)
        summary[lang] = {"asked": len(todo), "paired": paired}
        applog.aspect_pairs(lang, asked=len(todo), answered=len(found),
                            paired=paired, source=source)
    return summary


def fill_for_card(card):
    """Fill a verb card's pairs, both languages, after it is saved. Never
    raises: a lexicon failure must not cost the card, and the next run of the
    script picks up whatever was missed."""
    try:
        if not is_verb(card):
            return
        forms = {lang: single_word_variants(card.get(f"translation_{lang}"))
                 for lang in TEMPLATES}
        forms = {lang: f for lang, f in forms.items() if f}
        if forms:
            fill(forms, source="save")
    except Exception as error:  # noqa: BLE001 - never in the save's way
        applog.aspect_pairs("-", asked=0, answered=0, paired=0, source="save",
                            error=error)


def partner_map(lang, cards):
    """{normalised variant: partners} for the verb cards of a round, in one query."""
    forms = {normalise(v) for card in cards if is_verb(card)
             for v in single_word_variants(card.get(f"translation_{lang}"))}
    return utils.aspect_partners(lang, forms) if forms else {}
