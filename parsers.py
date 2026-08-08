"""
Parsers that turn external sources (Reverso Context lookups, OneNote .mht
exports, .txt and .docx notes) into flashcard entry dictionaries ready for
utils.save_flashcard().
"""

import email
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from email import policy
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import applog

REVERSO_URL = "https://context.reverso.net/translation/english-{lang}/{word}"
REVERSO_LANGS = {"ukr": "ukrainian", "rus": "russian"}
DEFINITION_URL = "https://dictionary.reverso.net/english-definition/{word}"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"

# Reverso blocks requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

MAX_TRANSLATIONS = 3
MAX_EXAMPLES = 3
MAX_EXAMPLES_FETCH = 12
MAX_DEFINITIONS = 3

# Reverso tags each translation element with a part-of-speech CSS class.
POS_CLASSES = {"n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb"}


def _fetch_reverso(word, lang):
    """
    Fetch one english-<lang> Reverso Context page.
    Returns (pos_translations, examples) where pos_translations maps a part
    of speech ('noun', 'verb', 'adjective', 'adverb', or 'other') to its top
    translations, and examples is a list of (english, target) sentence pairs.
    """
    url = REVERSO_URL.format(lang=lang, word=quote(word))
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    elements = soup.select("#translations-content .translation")
    if not elements:
        elements = soup.select("a.translation")

    pos_translations = {}
    for el in elements:
        term_el = el.select_one(".display-term")
        term = (term_el or el).get_text(strip=True)
        if not term:
            continue
        classes = set(el.get("class") or [])
        pos = next(
            (name for cls, name in POS_CLASSES.items() if cls in classes),
            "other",
        )
        terms = pos_translations.setdefault(pos, [])
        if term not in terms and len(terms) < MAX_TRANSLATIONS:
            terms.append(term)

    examples = []
    for example in soup.select("div.example")[:MAX_EXAMPLES_FETCH]:
        src = example.select_one(".src .text")
        trg = example.select_one(".trg .text")
        if src and trg:
            examples.append((
                src.get_text(" ", strip=True),
                trg.get_text(" ", strip=True),
            ))

    return pos_translations, examples


def _match_examples(terms, examples):
    """
    Pick example pairs whose target sentence contains one of the given
    translations, so each part-of-speech card gets examples of that usage.
    Returns (examples_en, examples_target), at most MAX_EXAMPLES each.
    """
    patterns = [
        re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in terms
    ]
    matched = [
        (en, target)
        for en, target in examples
        if any(pattern.search(target) for pattern in patterns)
    ][:MAX_EXAMPLES]
    return [en for en, _ in matched], [target for _, target in matched]


def _fetch_definitions(word):
    """
    Fetch short English definitions from Reverso's dictionary
    (dictionary.reverso.net), grouped by part of speech.
    Returns e.g. {'verb': ['simplify a process...'], 'noun': [...]}.
    Definitions marked 'very common' on the page are preferred.
    """
    url = DEFINITION_URL.format(word=quote(word))
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # POS labels and definition entries are siblings, so walk the page in
    # document order and remember which POS section we are in.
    collected = {}  # pos -> list of (is_common, text)
    current_pos = None
    for el in soup.select(".definition-pos-block__pos, .definition-example__def"):
        classes = el.get("class", [])
        if "definition-pos-block__pos" in classes:
            current_pos = el.get_text(strip=True).lower()
            continue
        if current_pos is None:
            continue
        # The clean sense text lives in the mention-sentence child; the
        # element also contains category/domain chips we don't want.
        sentence = el.select_one(".definition-example__mention-sentence")
        text = (sentence or el).get_text(" ", strip=True)
        if text:
            is_common = any("very-common" in cls for cls in classes)
            collected.setdefault(current_pos, []).append((is_common, text))

    definitions = {}
    for pos, entries in collected.items():
        ordered = [t for c, t in entries if c] + [t for c, t in entries if not c]
        defs = []
        for text in ordered:
            if text not in defs:
                defs.append(text)
            if len(defs) >= MAX_DEFINITIONS:
                break
        definitions[pos] = defs
    return definitions


def _google_translate(text, source, target):
    """
    Translate text with Google Translate's public JSON endpoint
    (no API key; fine for light personal use). Language codes are
    Google's own, e.g. 'ru', 'uk'.
    """
    params = {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
    resp = requests.get(GOOGLE_TRANSLATE_URL, params=params,
                        headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()


# Card-field suffix -> ISO 639-1 code. Google and Bing use the same codes.
GOOGLE_LANGS = {"ukr": "uk", "rus": "ru"}


def _google_dictionary(word, target):
    """
    Fetch dictionary-style translations for an English word from Google
    Translate (dt=bd returns entries grouped by part of speech).
    Returns e.g. {'noun': ['біг', ...], 'verb': ['бігати', ...]};
    words Google has no dictionary entry for fall back to
    {'other': [plain translation]}.
    """
    params = {
        "client": "gtx", "sl": "en", "tl": target, "hl": "en",
        "dt": ["t", "bd"], "q": word,
    }
    resp = requests.get(GOOGLE_TRANSLATE_URL, params=params,
                        headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    pos_translations = {}
    if len(data) > 1 and data[1]:
        for entry in data[1]:
            pos = (entry[0] or "other").lower()
            terms = [t for t in (entry[1] or []) if t][:MAX_TRANSLATIONS]
            if terms:
                pos_translations[pos] = terms

    if not pos_translations:
        plain = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
        if plain and plain.lower() != word.lower():
            pos_translations["other"] = [plain]
    return pos_translations


# --- Provider selection (issues #20 / #21) -----------------------------------
# The Settings popup lets the user pick a translator (Google / Bing) and an
# explanatory dictionary (Oxford / Merriam-Webster). Each option maps to one
# fetcher below, all sharing the contracts of the original Google and Reverso
# fetchers, so lookup_word() can treat every provider the same way.

# Bing's own web endpoints (bing.com/ttranslatev3, tlookupv3) reject
# non-browser TLS clients, so the Bing fetcher talks to the same Microsoft
# Translator engine the way the Edge browser's built-in translate feature
# does: a short-lived anonymous JWT from edge.microsoft.com, then the
# official dictionary/translate API. Verified to return exactly the same
# results as the bing.com translator page.
EDGE_AUTH_URL = "https://edge.microsoft.com/translate/auth"
BING_API_BASE = "https://api.cognitive.microsofttranslator.com"

# Microsoft's coarse POS tags -> the names used across the app's cards.
BING_POS = {"NOUN": "noun", "VERB": "verb", "ADJ": "adjective", "ADV": "adverb"}

# Auth tokens last ~10 minutes; cache one per process and renew early.
_bing_token = {"jwt": None, "expires": 0.0}


def _bing_auth_token():
    """Anonymous Microsoft Translator JWT (the Edge-translate auth flow)."""
    now = time.time()
    if _bing_token["jwt"] and now < _bing_token["expires"]:
        return _bing_token["jwt"]
    resp = requests.get(EDGE_AUTH_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    jwt = resp.text.strip()
    if not jwt:
        raise ValueError("Empty Microsoft Translator auth token")
    _bing_token.update(jwt=jwt, expires=now + 8 * 60)
    return jwt


def _bing_api(path, word, target):
    """One Microsoft Translator API call, retried once on a stale token."""
    for attempt in (1, 2):
        resp = requests.post(
            f"{BING_API_BASE}/{path}",
            params={"api-version": "3.0", "from": "en", "to": target},
            headers={**HEADERS, "Authorization": "Bearer " + _bing_auth_token()},
            json=[{"Text": word}],
            timeout=10,
        )
        if resp.status_code == 401 and attempt == 1:
            _bing_token["expires"] = 0.0  # token died early — mint a new one
            continue
        resp.raise_for_status()
        return resp.json()


def _bing_dictionary(word, target):
    """
    Bing Translator dictionary lookup (issue #21), grouped by part of speech —
    same contract as _google_dictionary(): {'noun': ['дім', ...], ...}.
    Words without a dictionary entry fall back to a plain translation under
    'other', mirroring the Google fetcher.
    """
    data = _bing_api("dictionary/lookup", word, target)

    pos_translations = {}
    for translation in data[0].get("translations", []):  # confidence-ordered
        pos = BING_POS.get((translation.get("posTag") or "").upper(), "other")
        term = translation.get("displayTarget")
        if not term:
            continue
        terms = pos_translations.setdefault(pos, [])
        if term not in terms and len(terms) < MAX_TRANSLATIONS:
            terms.append(term)

    if not pos_translations:
        data = _bing_api("translate", word, target)
        plain = (data[0]["translations"][0].get("text") or "").strip()
        if plain and plain.lower() != word.lower():
            pos_translations["other"] = [plain]
    return pos_translations


OXFORD_URL = "https://www.oxfordlearnersdictionaries.com/definition/english/{slug}"
# One Oxford entry page covers one part of speech (run -> run_1 is the verb,
# run_2 the noun), so a lookup may fetch a couple of sibling entry pages.
OXFORD_MAX_PAGES = 3


def _oxford_page_definitions(soup):
    """(pos, definitions, examples) of one Oxford Learner's entry page.

    Examples come from the same walk as the definitions (#225), because they live
    in the same place: every `ul.examples` sits inside an `li.sense`, never
    outside one, so a sense's sentences belong to it exactly as its definition
    does. Collecting them separately would mean a second pass over a page — or
    worse, a second fetch of it.

    A **descendant** selector, not `.sense > .def`. Oxford wraps a sense's
    definition in a `span.sensetop` whenever that sense carries extra furniture
    at the top of it — always on a single-sense entry, and on the first sense of
    many multi-sense ones. The definition is then a *grandchild* of `.sense`, and
    a direct-child selector walks straight past it:

        punctual   li.sense > span.sensetop > span.def   <- missed by `>`
        incentive  li.sense > span.def                   <- found by `>`

    Two failures came out of that, and the second is the worse one. A
    single-sense word returned no definition at all — 143 of the 360 words in
    `seed_words.py`, including `punctual`, `resign` and `algorithm`. And a
    multi-sense word could lose its *primary* sense while keeping a later one:
    `hedge` was defined only as a financial instrument, never as a row of
    bushes, which is a confidently wrong card rather than an empty one.

    It stayed hidden because `lookup_word()` falls back to Reverso's dictionary
    when this one returns nothing, and Reverso answers from a developer's
    machine. Only PythonAnywhere, where Reverso is IP-blocked, showed the words
    arriving with no explanation.
    """
    pos_el = soup.select_one(".webtop .pos")
    pos = pos_el.get_text(strip=True).lower() if pos_el else "other"
    defs = _capped(soup.select(".sense .def"), MAX_DEFINITIONS)
    # Oxford gives a popular word dozens of sentences — 41 for one of #203's
    # words — so this is trimming, not scraping. Its order is kept: the first
    # examples belong to the first sense, which is the one most likely wanted.
    examples = _capped(soup.select(".sense ul.examples > li"), MAX_EXAMPLES,
                       text=_oxford_example_text)
    return pos, defs, examples


def _oxford_example_text(item):
    """One Oxford example, without its grammar-pattern label.

    An example may carry a `span.cf` before the sentence — the pattern being
    illustrated, e.g. `be hedged (with something)`. Beside a dictionary heading
    that is useful; on a flashcard it reads as a broken sentence:

        be hedged (with something) His religious belief was always hedged...

    So only the `span.x` is taken. It falls back to the whole item when there is
    no `.x`, because an example is still worth having if Oxford changes how it
    wraps one.
    """
    sentence = item.select_one(".x")
    return (sentence or item).get_text(" ", strip=True)


def _capped(elements, limit, text=None):
    """The text of `elements`, de-duplicated, in order, at most `limit` of them.

    `text` extracts one element's string; the default takes all of it.
    """
    read = text or (lambda el: el.get_text(" ", strip=True))
    out = []
    for el in elements:
        value = read(el)
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _fetch_oxford_entry(word):
    """
    `(definitions, examples)` from Oxford Learner's Dictionaries, each
    `{pos: [text, ...]}` — issue #21 for the definitions, #225 for the examples.

    The base URL redirects to the word's first entry; entries for its other
    parts of speech are sibling pages (run_2, ...) linked from the page's
    'Other results' box, so up to OXFORD_MAX_PAGES pages are fetched.

    Both halves come from **one** pass over those pages. Definitions and
    examples live in the same `li.sense`, and an Oxford lookup is already up to
    three HTTP requests — asking twice, once for each, would double that to
    collect text we had in hand the first time.

    A part of speech with examples but no definition is still recorded. That is
    rare, but the two are independent and there is no reason to throw away
    sentences because the definition did not parse.
    """
    slug = quote(word.strip().lower().replace(" ", "-"))
    resp = requests.get(OXFORD_URL.format(slug=slug), headers=HEADERS, timeout=10)
    if resp.status_code == 404:
        return {}, {}  # word not in this dictionary
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    definitions, examples = {}, {}

    def collect(page):
        pos, defs, exs = _oxford_page_definitions(page)
        if defs:
            definitions.setdefault(pos, defs)
        if exs:
            examples.setdefault(pos, exs)

    collect(soup)

    entry_href = re.compile(rf"/definition/english/{re.escape(slug)}_\d+$")
    fetched = {resp.url.split("?")[0]}
    for link in soup.select("#relatedentries a"):
        href = (link.get("href") or "").split("?")[0]
        if not entry_href.search(href) or href in fetched:
            continue
        if len(fetched) >= OXFORD_MAX_PAGES:
            break
        fetched.add(href)
        try:
            sibling = requests.get(href, headers=HEADERS, timeout=10)
            sibling.raise_for_status()
        except requests.RequestException:
            continue  # the entries already collected are still useful
        collect(BeautifulSoup(sibling.text, "lxml"))
    return definitions, examples


def _fetch_oxford_definitions(word):
    """English definitions from Oxford, by part of speech (issue #21).

    The **shared dictionary-backend contract**: the same `{pos: [defs]}` shape
    `_fetch_definitions()` (Reverso) and `_fetch_merriam_webster_definitions()`
    return, which is what lets `_dictionary_backend()` treat every provider the
    same way. Kept as its own function for that reason — `lookup_word()` reaches
    past it to `_fetch_oxford_entry()` when Oxford is the chosen dictionary,
    because Oxford is the only one of the three that also has examples (#225).
    """
    return _fetch_oxford_entry(word)[0]


MERRIAM_WEBSTER_URL = "https://www.merriam-webster.com/dictionary/{word}"


def _fetch_merriam_webster_definitions(word):
    """
    English definitions from Merriam-Webster (issue #21), grouped by part of
    speech — same contract as the Reverso _fetch_definitions(). Unlike
    Oxford, one page carries every entry (run: verb, noun, adjective).
    """
    url = MERRIAM_WEBSTER_URL.format(word=quote(word.strip().lower()))
    resp = requests.get(url, headers=HEADERS, timeout=10)
    if resp.status_code == 404:
        return {}  # M-W answers unknown words with a 404 suggestions page
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    definitions = {}
    for entry in soup.select("div[id^=dictionary-entry]"):
        pos_el = (entry.select_one(".parts-of-speech a")
                  or entry.select_one(".parts-of-speech"))
        if pos_el is None:
            continue
        # 'verb (2 of 3)' -> 'verb'
        pos = pos_el.get_text(" ", strip=True).split()[0].lower()
        defs = definitions.setdefault(pos, [])
        for sense in entry.select(".dtText"):
            text = sense.get_text(" ", strip=True).lstrip(":").strip()
            if text and text not in defs:
                defs.append(text)
            if len(defs) >= MAX_DEFINITIONS:
                break
    return {pos: defs for pos, defs in definitions.items() if defs}


# Option value (as stored in the settings file, #86) -> fetcher. Resolved at
# call time (not captured in a module-level dict) so tests can monkeypatch a
# single fetcher and the dispatch picks the replacement up.
def _translator_backend(translator):
    return {
        "google": _google_dictionary,
        "bing": _bing_dictionary,
    }.get(translator, _google_dictionary)


def _merriam_webster_entry(word):
    """Merriam-Webster in the entry shape — definitions, and no examples (#225).

    It does have examples on the page; extracting them is not this ticket's, and
    it is unreachable from PythonAnywhere anyway. Returning an empty second half
    keeps every dictionary backend the same shape, which is what lets
    `lookup_word()` have one seam instead of a branch per provider.
    """
    return _fetch_merriam_webster_definitions(word), {}


def _dictionary_backend(explanatory_dictionary):
    """The chosen dictionary, as a function returning `(definitions, examples)`.

    Entry-shaped rather than definitions-only since #225. That matters beyond
    tidiness: whatever this returns is the *only* thing `lookup_word()` calls, so
    it is also the seam the test suite stubs. An earlier attempt kept this
    returning `_fetch_oxford_definitions` and had `lookup_word()` reach past it to
    the richer fetcher — which meant a stubbed backend was bypassed and the
    offline tests silently made live requests to Oxford.
    """
    return {
        "oxford": _fetch_oxford_entry,
        "merriam-webster": _merriam_webster_entry,
    }.get(explanatory_dictionary, _fetch_oxford_entry)


def _provider_name(backend):
    """The site a backend actually talks to — what dict.log records (#30).
    Resolved from the function so an unknown setting is logged as the provider
    that really ran, not the one that was asked for."""
    return {
        _google_dictionary: "google",
        _bing_dictionary: "bing",
        _fetch_oxford_entry: "oxford",
        _merriam_webster_entry: "merriam-webster",
        # The definitions-only fetchers, still reached directly: Reverso as
        # lookup_word()'s fallback, Oxford by seed_topics.py --check-oxford.
        _fetch_oxford_definitions: "oxford",
        _fetch_merriam_webster_definitions: "merriam-webster",
        _fetch_definitions: "reverso",
    }.get(backend, getattr(backend, "__name__", "unknown"))


def lookup_word(word, topic=None, translator="google", explanatory_dictionary="oxford"):
    """
    Build flashcard entries (English→Ukrainian and English→Russian) with the
    selected providers (issue #20) — one card per part of speech, same shape
    as parse_reverso_word() produces.

    Translations come from the chosen translator backend, falling back to
    Google Translate when that backend fails or returns nothing (e.g. the
    provider is unreachable from the server). English definitions come from
    the chosen explanatory dictionary, with Reverso's dictionary as the
    fallback; a lookup without definitions is still useful, so definition
    failures never break the lookup.
    """
    overall = applog.Timer()
    fetch_translations = _translator_backend(translator)
    primary = _provider_name(fetch_translations)
    cards = {}  # pos -> entry dict

    for key, code in GOOGLE_LANGS.items():
        error = None
        with applog.Timer() as timer:
            try:
                pos_translations = fetch_translations(word, code)
            except (requests.RequestException, ValueError) as e:
                pos_translations = {}
                error = e
        applog.translations_fetched(word, primary, code, len(pos_translations),
                                    timer.ms, error=error)
        if not pos_translations and fetch_translations is not _google_dictionary:
            error = None
            with applog.Timer() as timer:
                try:
                    pos_translations = _google_dictionary(word, code)
                except (requests.RequestException, ValueError) as e:
                    error = e
            applog.translations_fetched(word, "google", code,
                                        len(pos_translations), timer.ms,
                                        fallback_from=primary, error=error)
        for pos, terms in pos_translations.items():
            entry = cards.setdefault(pos, {"word": word, "pos": pos, "topic": topic})
            entry[f"translation_{key}"] = ", ".join(terms)

    # Same rule as the Reverso parser: the untagged catch-all card is kept
    # only when no part of speech was identified at all.
    if len(cards) > 1:
        cards.pop("other", None)

    if not cards:
        applog.lookup_failed(word, "no translations")
        raise ValueError(f"No translations found for '{word}'")

    fetch_defs = _dictionary_backend(explanatory_dictionary)
    dictionary = _provider_name(fetch_defs)
    error = None
    examples = {}
    with applog.Timer() as timer:
        try:
            # One call, both halves (#225): a dictionary's examples come out of
            # the pages its definitions do, and Oxford already costs up to three
            # requests, so asking twice would double that for text we had.
            definitions, examples = fetch_defs(word)
        except (requests.RequestException, ValueError) as e:
            definitions, examples = {}, {}
            error = e
    applog.definitions_fetched(word, dictionary, len(definitions), timer.ms,
                               error=error)
    if not definitions:
        error = None
        with applog.Timer() as timer:
            try:
                definitions = _fetch_definitions(word)
            except requests.RequestException as e:
                definitions = {}  # Reverso blocks datacenter IPs — skip quietly
                error = e
        applog.definitions_fetched(word, "reverso", len(definitions), timer.ms,
                                   fallback_from=dictionary, error=error)
    for pos, defs in definitions.items():
        if pos in cards:
            cards[pos]["explanation_en"] = "; ".join(defs)
    # A list, not a joined string: `examples_en` is one of utils.LIST_FIELDS and
    # is stored as JSON, so the card page can show them as separate sentences.
    # Only where the translator found the same part of speech — the same rule the
    # definitions follow, and why a card can have a translation and neither.
    for pos, sentences in examples.items():
        if pos in cards:
            cards[pos]["examples_en"] = sentences

    applog.lookup_finished(word, len(cards), overall.ms)
    return list(cards.values())


def parse_google_word(word, topic=None):
    """
    Build flashcard entries from Google Translate's dictionary data — kept as
    a thin wrapper around lookup_word() with the default providers, for code
    (and tests) written before provider selection existed.
    """
    return lookup_word(word, topic=topic)


def _fill_missing_translation(entry):
    """
    If a card has only one of the Russian/Ukrainian translations, fill the
    other by machine-translating between the two (ru <-> uk).
    Failures are ignored — the card is still useful with one language.
    """
    rus, ukr = entry.get("translation_rus"), entry.get("translation_ukr")
    try:
        if rus and not ukr:
            entry["translation_ukr"] = _google_translate(rus, "ru", "uk") or None
        elif ukr and not rus:
            entry["translation_rus"] = _google_translate(ukr, "uk", "ru") or None
    except (requests.RequestException, ValueError):
        pass


def parse_reverso_word(word, topic=None):
    """
    Look up a word on Reverso Context (English→Ukrainian and English→Russian)
    and build flashcard entries — one per part of speech found, so e.g.
    'run' produces separate verb and noun cards. English definitions from
    Reverso's dictionary are attached to the matching cards, and a missing
    Russian/Ukrainian translation is filled via Google Translate.
    """
    cards = {}  # pos -> entry dict

    for key, lang in REVERSO_LANGS.items():
        try:
            pos_translations, examples = _fetch_reverso(word, lang)
        except requests.RequestException:
            continue  # keep the other language even if one lookup fails

        for pos, terms in pos_translations.items():
            entry = cards.setdefault(pos, {"word": word, "pos": pos, "topic": topic})
            entry[f"translation_{key}"] = ", ".join(terms)

            examples_en, examples_target = _match_examples(terms, examples)
            if not examples_target and len(pos_translations) == 1:
                # single-POS word: no need to disambiguate, take the top examples
                examples_en = [en for en, _ in examples[:MAX_EXAMPLES]]
                examples_target = [t for _, t in examples[:MAX_EXAMPLES]]
            if examples_target:
                entry[f"examples_{key}"] = examples_target
            if examples_en and "examples_en" not in entry:
                entry["examples_en"] = examples_en

    # Untagged translations are mostly inflected duplicates of the tagged
    # cards, so keep the catch-all card only when Reverso tagged nothing.
    if len(cards) > 1:
        cards.pop("other", None)

    if not cards:
        raise ValueError(f"No Reverso results found for '{word}'")

    try:
        definitions = _fetch_definitions(word)
    except requests.RequestException:
        definitions = {}  # cards are still useful without definitions
    for pos, defs in definitions.items():
        if pos in cards:
            cards[pos]["explanation_en"] = "; ".join(defs)

    for entry in cards.values():
        _fill_missing_translation(entry)

    return list(cards.values())


SEPARATORS = (" — ", " – ", " - ", ": ")


def _entry_from_line(text, topic):
    """
    Turn one line of note text into an entry if it looks like
    'word — explanation' (also accepts -, – and : as separators).

    A Cyrillic right-hand side is a translation rather than an English
    explanation ('pursuit - преследование', #137), so it is stored in the
    matching translation field instead.
    """
    if not text:
        return None
    for sep in SEPARATORS:
        if sep in text:
            word, rest = text.split(sep, 1)
            word = word.strip()
            rest = rest.strip()
            if word and rest and len(word) <= 80:
                entry = {"word": word, "topic": topic}
                if _has_cyrillic(rest):
                    lang = _detect_cyrillic_lang(rest) or "rus"
                    entry[f"translation_{lang}"] = rest
                else:
                    entry["explanation_en"] = rest
                return entry
    return None


TEXT_NODES = ["p", "li", "td", "h1", "h2", "h3", "h4"]


def _mht_soup(data):
    """Decode an .mht (MIME HTML) byte payload and return its HTML as soup."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    msg = email.message_from_bytes(data, policy=policy.default)

    html = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_content()
            break
    if html is None:
        raise ValueError("No HTML content found in the MHT file")
    return BeautifulSoup(html, "lxml")


def _entries_from_soup(soup, topic):
    """Extract flashcard entries from 'word — explanation' lines in the soup.

    Used by parse_mht_file(); the review-popup path goes through
    _cards_from_lines(), which also understands Reverso copy-pastes.
    """
    entries = []
    seen_words = set()
    for node in soup.find_all(["p", "li", "td"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        entry = _entry_from_line(text, topic)
        if entry and entry["word"].lower() not in seen_words:
            seen_words.add(entry["word"].lower())
            entries.append(entry)
    return entries


def _readable_text(soup):
    """The document's visible text, one line per block — shown in the review
    popup next to the parsed cards so the user can see the source."""
    lines = []
    for node in soup.find_all(TEXT_NODES):
        text = " ".join(node.get_text(" ", strip=True).split())
        if text:
            lines.append(text)
    if not lines:  # fallback for documents without those block tags
        raw = soup.get_text("\n", strip=True)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return "\n".join(lines)


# --- Reverso copy-paste parsing (issue #134) --------------------------------
# OneNote .mht copy-pastes of Reverso dictionary entries have a fixed,
# colour-coded structure (one <p> per line):
#   <word> <POS ru/uk>     header — the POS <span> is coloured REVERSO_POS_COLOUR
#   N.                     sense marker
#   (context) English def  explanation   (#222C31)
#   English usage sentence example       (#546D79)
#   Cyrillic translation   ex_tr         (#0A6CC2)
#   концатенированные      translations  (#2E3C43) — glued together, no spaces
# One card is built per word+POS with all senses aggregated. The glued
# translation terms can't be split from the markup, so Claude splits them (per
# the issue); without an API key the whole line is kept as a single term.

REVERSO_POS_COLOUR = "#607D8B"
REVERSO_LINE_COLOURS = {
    "#0A6CC2": "ex_tr", "#2E3C43": "translations",
    "#222C31": "explanation", "#546D79": "example",
}
REVERSO_POS_MAP = {
    "существительное": "noun", "прилагательное": "adjective", "глагол": "verb",
    "наречие": "adverb", "местоимение": "pronoun", "предлог": "preposition",
    "союз": "conjunction", "числительное": "numeral",
    "междометие": "interjection", "причастие": "participle",
    "іменник": "noun", "прикметник": "adjective", "дієслово": "verb",
    "прислівник": "adverb", "займенник": "pronoun", "прийменник": "preposition",
    "сполучник": "conjunction", "числівник": "numeral",
    "вигук": "interjection", "дієприкметник": "participle",
}
_UK_ONLY = set("іїєґ")
_RU_ONLY = set("ыэъё")
SPLIT_MODEL = "claude-haiku-4-5-20251001"


def _p_colour(el):
    m = re.search(r"color:(#[0-9A-Fa-f]{6})", el.get("style") or "")
    return m.group(1).upper() if m else ""


def _reverso_header(p):
    """(word, pos_raw) if this <p> is a Reverso word/POS header, else None."""
    for s in p.find_all("span"):
        if _p_colour(s) == REVERSO_POS_COLOUR:
            pos = s.get_text(" ", strip=True)
            text = " ".join(p.get_text(" ", strip=True).split())
            word = text[: text.rfind(pos)].strip() if pos and pos in text else ""
            return (word, pos) if word and pos else None
    return None


def _clean_explanation(text):
    # get_text(" ") pads the italic "(context)" span -> "( context )"; tidy it.
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text)).strip()


def _new_sense():
    return {"explanation": None, "example": None, "ex_tr": None,
            "translations": None, "terms": None}


def _reverso_entries_from_lines(lines):
    """
    The Reverso state machine, shared by every notes format (#134, #137).

    `lines` are dicts describing one source line each:
      text    — the line's text (already whitespace-normalised)
      header  — (word, pos_raw) if the line opens a new word entry
      sense   — True if the line opens a new sense ("1.", "2.", …)
      field   — which sense field the text fills: explanation / example /
                ex_tr / translations (None = not part of an entry)
      terms   — translation terms already separated (plain text lists them one
                per line, so they need no AI splitting)

    Colour-coded formats (.mht, .docx) classify lines by the Reverso palette,
    plain text by its structure — the state machine itself is format-agnostic.

    Returns (entries, consumed): `consumed` is the set of line indices the
    machine claimed, so the caller can run the 'word — explanation' line parser
    over everything left over (a notes file may mix both styles, #137).
    """
    entries, entry, sense = [], None, None
    for i, line in enumerate(lines):
        text = line["text"]
        if not text:
            continue
        if line.get("header"):
            word, pos_raw = line["header"]
            entry = {"word": word, "pos_raw": pos_raw, "senses": [], "lines": [i]}
            entries.append(entry)
            sense = None
            continue
        if entry is None:
            continue
        field = line.get("field")
        if line.get("sense"):
            sense = _new_sense()
            entry["senses"].append(sense)
            entry["lines"].append(i)
        elif sense is None:
            # Reverso omits the "1." marker when a word has a single sense
            # (#137) — the first explanation line opens the sense implicitly.
            if field != "explanation":
                continue
            sense = _new_sense()
            entry["senses"].append(sense)
        if not field:
            continue
        entry["lines"].append(i)
        if line.get("terms"):
            sense["terms"] = (sense["terms"] or []) + line["terms"]
        elif sense.get(field) is None:
            sense[field] = _clean_explanation(text) if field == "explanation" else text

    entries = [e for e in entries if e["senses"]]
    consumed = {i for e in entries for i in e["lines"]}
    return entries, consumed


def _reverso_lines_from_soup(soup):
    """Classify an .mht document's lines by the Reverso colour palette.

    Only <p> ever carries the palette; <li>/<td> are included so plain notes
    keep reaching the 'word — explanation' parser as they always did.
    """
    lines = []
    for p in soup.find_all(["p", "li", "td"]):
        text = " ".join(p.get_text(" ", strip=True).split())
        marker = bool(re.match(r"^\d+\.$", text))
        lines.append({
            "text": text,
            "header": _reverso_header(p),
            "sense": marker,
            # a bare "N." carries no content of its own, whatever its colour
            "field": None if marker else REVERSO_LINE_COLOURS.get(_p_colour(p)),
        })
    return lines


def _reverso_entries(soup):
    """[{word, pos_raw, senses:[…]}] for an .mht soup."""
    return _reverso_entries_from_lines(_reverso_lines_from_soup(soup))[0]


def _has_cyrillic(text):
    return bool(re.search(r"[Ѐ-ӿ]", text or ""))


def _detect_cyrillic_lang(*texts):
    joined = " ".join(t for t in texts if t).lower()
    if any(ch in _UK_ONLY for ch in joined):
        return "ukr"
    if any(ch in _RU_ONLY for ch in joined):
        return "rus"
    return None


def _split_glued_translations(strings):
    """Split each glued Reverso translation string into its terms with Claude
    (#134) — multi-word phrases stay together. Any failure (no API key,
    offline, malformed reply) falls back to keeping the whole string as one
    term, so parsing never breaks."""
    strings = [s for s in dict.fromkeys(strings) if s]
    fallback = {s: [s] for s in strings}
    if not strings:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic()
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(strings))
        prompt = (
            "Below are lists of dictionary translations copied from Reverso. In "
            "each line the individual translation terms were concatenated "
            "without any separator. Split each line into its distinct "
            "translation terms. A term may itself be a multi-word phrase (e.g. "
            "'верховный правитель') — keep such phrases together. Do not "
            "invent, translate, reorder or drop anything; only insert the "
            "boundaries. Reply with ONLY a JSON object mapping each line number "
            '(as a string) to an array of terms, e.g. {"0": ["term1", '
            '"term2"]}.\n\n' + numbered
        )
        msg = client.messages.create(
            model=SPLIT_MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        result = dict(fallback)
        for k, v in data.items():
            terms = [t.strip() for t in v if isinstance(t, str) and t.strip()]
            if terms and 0 <= int(k) < len(strings):
                result[strings[int(k)]] = terms
        applog.terms_split(len(strings), sum(len(t) for t in result.values()),
                           model=SPLIT_MODEL)
        return result
    except Exception as e:
        # The whole line is kept as one term; log why, since the fallback is
        # silent by design and the result looks like a parser bug (#30).
        applog.terms_split(len(strings), len(strings), model=SPLIT_MODEL,
                           error=e)
        return fallback


def _reverso_cards(entries, topic):
    """One card per word+POS, senses aggregated; glued translations AI-split."""
    # Only the colour-coded formats glue the terms together; plain text lists
    # them one per line, so those senses skip the AI split entirely (#137).
    splits = _split_glued_translations(
        [s["translations"] for e in entries for s in e["senses"]
         if s["translations"]]
    )
    cards = []
    for e in entries:
        pos_raw = e["pos_raw"].strip()
        # plain-text copy-pastes often carry no POS at all — leave it unset
        # rather than labelling the card "other" (#137)
        pos = REVERSO_POS_MAP.get(pos_raw.lower(), "other") if pos_raw else None
        expl, ex_en, ex_tr, terms, hints = [], [], [], [], []
        for s in e["senses"]:
            if s["explanation"]:
                expl.append(s["explanation"])
            if s["example"]:
                ex_en.append(s["example"])
            if s["ex_tr"]:
                ex_tr.append(s["ex_tr"])
                hints.append(s["ex_tr"])
            if s["translations"]:
                hints.append(s["translations"])
                for t in splits.get(s["translations"], [s["translations"]]):
                    if t not in terms:
                        terms.append(t)
            for t in s["terms"] or []:
                hints.append(t)
                if t not in terms:
                    terms.append(t)
        lang = _detect_cyrillic_lang(*hints) or "rus"
        card = {"word": e["word"], "pos": pos, "topic": topic}
        if expl:
            card["explanation_en"] = "; ".join(expl)
        if ex_en:
            card["examples_en"] = ex_en
        if terms:
            card[f"translation_{lang}"] = ", ".join(terms)
        if ex_tr:
            card[f"examples_{lang}"] = ex_tr
        cards.append(card)
    return cards


def parse_mht_file(data, topic=None):
    """
    Parse an .mht (MIME HTML, e.g. OneNote export) file and extract flashcard
    entries from lines shaped like 'word — explanation'.

    `data` is the raw file content as bytes. Returns a list of entry dicts.
    """
    return _entries_from_soup(_mht_soup(data), topic)


def _cards_from_lines(lines, topic):
    """
    Build the cards for one notes document, in document order.

    Reverso copy-pastes are parsed by the state machine (POS, senses,
    examples, translations); every line it does not claim is offered to the
    plain 'word — explanation' parser, so a file may mix both styles (#137).
    Words are de-duplicated, the richer Reverso card winning.
    """
    entries, consumed = _reverso_entries_from_lines(lines)
    cards = _reverso_cards(entries, topic)
    found = [(e["lines"][0], c) for e, c in zip(entries, cards)]
    seen_words = {c["word"].lower() for c in cards}
    for i, line in enumerate(lines):
        if i in consumed:
            continue
        entry = _entry_from_line(line["text"], topic)
        if entry and entry["word"].lower() not in seen_words:
            seen_words.add(entry["word"].lower())
            found.append((i, entry))
    found.sort(key=lambda pair: pair[0])
    return [card for _, card in found]


def parse_mht_preview(data, topic=None):
    """
    Like parse_mht_file, but also return the file's readable text so the UI can
    show the source alongside the parsed cards for review before saving.

    Reverso dictionary copy-pastes (issue #134) are parsed with the richer
    Reverso parser (POS, senses, examples, AI-split translations); anything
    else falls back to the 'word — explanation' line parser.

    Returns (entries, source_text).
    """
    soup = _mht_soup(data)
    return _cards_from_lines(_reverso_lines_from_soup(soup), topic), _readable_text(soup)


# --- .txt and .docx notes (issue #137) --------------------------------------
# Same two styles as .mht, in formats that carry less structure:
#   .docx keeps Reverso's colours in the run properties, so it reuses the
#         colour classifier;
#   .txt  has no formatting at all, so its lines are classified by shape
#         (see _reverso_lines_from_text).

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "latin-1")
SENSE_RE = re.compile(r"^(\d+)[.)]\s*(.*)$")
DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _decode_text(data):
    """Decode uploaded note bytes; latin-1 accepts anything, so it can't fail."""
    if isinstance(data, str):
        return data
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace")


def _trailing_pos(text):
    """The Cyrillic part-of-speech word a Reverso header ends with, if any."""
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].lower() in REVERSO_POS_MAP:
        return parts[1]
    return None


def _reverso_lines_from_text(texts):
    """
    Classify plain-text lines, which carry no colours, by their shape.

    A Reverso block copied as text looks like:

        lucid dream                                  <- header
        1. (awareness) dream where you know …        <- sense + explanation
        In a lucid dream, she flew over mountains.   <- English example
        Во вещем сне она летала над горами.          <- its translation
        вещий сон                                    <- translation terms,
        осознанный сон                                  one per line

    A header is a line ending in a Cyrillic part-of-speech name ("fount
    Существительное"), or the line directly above a "1." sense marker. A blank
    line ends the block, so ordinary 'word — translation' notes around it are
    left to the line parser.
    """
    texts = [" ".join((t or "").split()) for t in texts]
    headers = {}
    for i, text in enumerate(texts):
        if not text:
            continue
        pos = _trailing_pos(text)
        if pos:
            word = text[: -len(pos)].strip()
            if word:
                headers[i] = (word, pos)
        elif (i and texts[i - 1] and (i - 1) not in headers
                and not SENSE_RE.match(texts[i - 1])):
            # "1." opens a block, so the line right above it is the header
            # (later senses continue the block and must not restart it)
            match = SENSE_RE.match(text)
            if match and match.group(1) == "1":
                headers[i - 1] = (texts[i - 1], "")

    lines = []
    inside = sense_open = example_seen = ex_tr_seen = False
    for i, text in enumerate(texts):
        line = {"text": text, "header": None, "sense": False,
                "field": None, "terms": None}
        lines.append(line)
        if not text:
            inside = False          # a blank line closes the block
            continue
        if i in headers:
            line["header"] = headers[i]
            inside, sense_open = True, False
            continue
        if not inside:
            continue
        match = SENSE_RE.match(text)
        if match:
            line["sense"] = True
            sense_open, example_seen, ex_tr_seen = True, False, False
            if match.group(2):      # "1. (context) explanation" on one line
                line["text"] = match.group(2)
                line["field"] = "explanation"
            continue
        if not sense_open:          # single-sense block: no "N." marker
            line["sense"] = True
            line["field"] = "explanation"
            sense_open = True
            continue
        if not _has_cyrillic(text):
            if not example_seen:
                line["field"] = "example"
                example_seen = True
        elif example_seen and not ex_tr_seen:
            line["field"] = "ex_tr"
            ex_tr_seen = True
        else:
            line["field"] = "translations"
            line["terms"] = [text]
    return lines


def _docx_paragraphs(data):
    """
    [(text, [(run_text, colour), …])] for every paragraph of a .docx, in
    document order (table cells included — a cell simply wraps paragraphs).

    Read with the standard library rather than python-docx: the app would
    otherwise gain a runtime dependency just to reach the run colours (#137).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    paragraphs = []
    for node in ET.fromstring(xml).iter(DOCX_NS + "p"):
        runs = []
        for run in node.iter(DOCX_NS + "r"):
            text = "".join(t.text or "" for t in run.iter(DOCX_NS + "t"))
            if not text:
                continue
            colour = ""
            properties = run.find(DOCX_NS + "rPr")
            if properties is not None:
                element = properties.find(DOCX_NS + "color")
                value = (element.get(DOCX_NS + "val") or "") if element is not None else ""
                if value and value.lower() != "auto":
                    colour = "#" + value.upper()
            runs.append((text, colour))
        paragraphs.append(("".join(text for text, _ in runs), runs))
    return paragraphs


def _reverso_lines_from_docx(paragraphs):
    """Classify .docx paragraphs by the Reverso colour palette, exactly as the
    .mht path does — Word keeps the colours in the runs' properties."""
    lines = []
    for text, runs in paragraphs:
        text = " ".join(text.split())
        header = None
        pos = next((" ".join(t.split()) for t, colour in runs
                    if colour == REVERSO_POS_COLOUR), None)
        if pos and pos in text:
            word = text[: text.rfind(pos)].strip()
            if word:
                header = (word, pos)
        # a paragraph's colour is the one covering most of its text
        coloured = {}
        for run_text, colour in runs:
            if colour:
                coloured[colour] = coloured.get(colour, 0) + len(run_text)
        colour = max(coloured, key=coloured.get) if coloured else ""
        marker = bool(re.match(r"^\d+\.$", text))
        lines.append({
            "text": text,
            "header": header,
            "sense": marker,
            # Word colours the "N." markers like examples — they hold no text
            "field": None if marker else REVERSO_LINE_COLOURS.get(colour),
        })
    return lines


def parse_txt_preview(data, topic=None):
    """Parse a plain-text notes file. Returns (entries, source_text)."""
    text = _decode_text(data)
    lines = _reverso_lines_from_text(text.splitlines())
    return _cards_from_lines(lines, topic), text.strip()


def parse_docx_preview(data, topic=None):
    """Parse a Word (.docx) notes file. Returns (entries, source_text)."""
    paragraphs = _docx_paragraphs(data)
    lines = _reverso_lines_from_docx(paragraphs)
    source = "\n".join(line["text"] for line in lines if line["text"])
    return _cards_from_lines(lines, topic), source


NOTES_PARSERS = {
    ".mht": parse_mht_preview,
    ".mhtml": parse_mht_preview,
    ".txt": parse_txt_preview,
    ".docx": parse_docx_preview,
}


def parse_notes_preview(filename, data, topic=None):
    """
    Parse an uploaded notes file, choosing the parser by extension (#137).

    Raises ValueError for anything but .mht/.mhtml/.txt/.docx.
    Returns (entries, source_text).
    """
    extension = os.path.splitext(filename or "")[1].lower()
    parser = NOTES_PARSERS.get(extension)
    if parser is None:
        raise ValueError(f"Unsupported notes format: {extension or filename}")
    return parser(data, topic=topic)
