"""
Parsers that turn external sources (Reverso Context lookups, OneNote .mht
exports) into flashcard entry dictionaries ready for utils.save_flashcard().
"""

import email
import re
import time
from email import policy
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

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
    """(pos, definitions) of one Oxford Learner's entry page."""
    pos_el = soup.select_one(".webtop .pos")
    pos = pos_el.get_text(strip=True).lower() if pos_el else "other"
    defs = []
    for el in soup.select(".sense > .def"):
        text = el.get_text(" ", strip=True)
        if text and text not in defs:
            defs.append(text)
        if len(defs) >= MAX_DEFINITIONS:
            break
    return pos, defs


def _fetch_oxford_definitions(word):
    """
    English definitions from Oxford Learner's Dictionaries (issue #21), by
    part of speech — same contract as the Reverso _fetch_definitions().

    The base URL redirects to the word's first entry; entries for its other
    parts of speech are sibling pages (run_2, ...) linked from the page's
    'Other results' box, so up to OXFORD_MAX_PAGES pages are fetched.
    """
    slug = quote(word.strip().lower().replace(" ", "-"))
    resp = requests.get(OXFORD_URL.format(slug=slug), headers=HEADERS, timeout=10)
    if resp.status_code == 404:
        return {}  # word not in this dictionary
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    definitions = {}
    pos, defs = _oxford_page_definitions(soup)
    if defs:
        definitions[pos] = defs

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
        pos, defs = _oxford_page_definitions(BeautifulSoup(sibling.text, "lxml"))
        if defs:
            definitions.setdefault(pos, defs)
    return definitions


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


def _dictionary_backend(explanatory_dictionary):
    return {
        "oxford": _fetch_oxford_definitions,
        "merriam-webster": _fetch_merriam_webster_definitions,
    }.get(explanatory_dictionary, _fetch_oxford_definitions)


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
    fetch_translations = _translator_backend(translator)
    cards = {}  # pos -> entry dict

    for key, code in GOOGLE_LANGS.items():
        try:
            pos_translations = fetch_translations(word, code)
        except (requests.RequestException, ValueError):
            pos_translations = {}
        if not pos_translations and fetch_translations is not _google_dictionary:
            try:
                pos_translations = _google_dictionary(word, code)
            except (requests.RequestException, ValueError):
                pass
        for pos, terms in pos_translations.items():
            entry = cards.setdefault(pos, {"word": word, "pos": pos, "topic": topic})
            entry[f"translation_{key}"] = ", ".join(terms)

    # Same rule as the Reverso parser: the untagged catch-all card is kept
    # only when no part of speech was identified at all.
    if len(cards) > 1:
        cards.pop("other", None)

    if not cards:
        raise ValueError(f"No translations found for '{word}'")

    fetch_defs = _dictionary_backend(explanatory_dictionary)
    try:
        definitions = fetch_defs(word)
    except (requests.RequestException, ValueError):
        definitions = {}
    if not definitions:
        try:
            definitions = _fetch_definitions(word)
        except requests.RequestException:
            definitions = {}  # e.g. Reverso blocks datacenter IPs — skip quietly
    for pos, defs in definitions.items():
        if pos in cards:
            cards[pos]["explanation_en"] = "; ".join(defs)

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
    """
    if not text:
        return None
    for sep in SEPARATORS:
        if sep in text:
            word, explanation = text.split(sep, 1)
            word = word.strip()
            explanation = explanation.strip()
            if word and explanation and len(word) <= 80:
                return {
                    "word": word,
                    "explanation_en": explanation,
                    "topic": topic,
                }
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
    """Extract flashcard entries from 'word — explanation' lines in the soup."""
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


def parse_mht_file(data, topic=None):
    """
    Parse an .mht (MIME HTML, e.g. OneNote export) file and extract flashcard
    entries from lines shaped like 'word — explanation'.

    `data` is the raw file content as bytes. Returns a list of entry dicts.
    """
    return _entries_from_soup(_mht_soup(data), topic)


def parse_mht_preview(data, topic=None):
    """
    Like parse_mht_file, but also return the file's readable text so the UI can
    show the source alongside the parsed cards for review before saving.

    Returns (entries, source_text).
    """
    soup = _mht_soup(data)
    return _entries_from_soup(soup, topic), _readable_text(soup)
