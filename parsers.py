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
from typing import NamedTuple
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


# Punctuation a space must not precede, and brackets a space must not follow
# (#359). The ellipsis is deliberately absent: `...` is Oxford's omission
# marker rather than the end of a sentence, and "It's about time you ..." is
# how Oxford writes it.
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?)\]])")
_SPACE_AFTER_BRACKET = re.compile(r"([(\[])\s+")


def _readable(text):
    """One scraped string, tidied of the separator's own footprints (#359).

    Every extraction below reads a node as `get_text(" ", strip=True)`, and
    the separator is load-bearing - without it Oxford's markup glues words
    together, `...help with literacy and <span>numeracy</span>.` coming out as
    `literacy andnumeracy.`. The cost is a space wherever an element ends and
    punctuation follows, which is exactly what happens when a sentence ends on
    the word being looked up:

        Are your grandparents still alive ?

    So it is not sporadic, it is systematic: a lookup's own examples are the
    ones most likely to end on the headword, and all three for `alive` did.
    Same footprint around a parenthetical of its own - `( = spread quickly )`.
    """
    collapsed = " ".join(text.split())
    tidied = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", collapsed)
    return _SPACE_AFTER_BRACKET.sub(r"\1", tidied)


def _node_text(el):
    """The readable text of one element - the single seam where scraped
    markup becomes something a learner reads, so the tidy-up happens once
    rather than in whichever caller last noticed it was needed."""
    return _readable(el.get_text(" ", strip=True))


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
                _node_text(src),
                _node_text(trg),
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
        text = _node_text(sentence or el)
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
    pos = _oxford_poses(pos_el.get_text(strip=True).lower()) if pos_el else ["other"]
    defs = _capped(soup.select(".sense .def"), MAX_DEFINITIONS)
    # Oxford gives a popular word dozens of sentences — 41 for one of #203's
    # words — so this is trimming, not scraping. Its order is kept: the first
    # examples belong to the first sense, which is the one most likely wanted.
    examples = _capped(soup.select(".sense ul.examples > li"), MAX_EXAMPLES,
                       text=_oxford_example_text)
    return pos, defs, examples


def _oxford_poses(text):
    """The parts of speech one Oxford entry covers (#228).

    Usually one, but an entry can carry several: `both` is headed
    "determiner, pronoun" and `hello` "exclamation, noun", and its definitions
    belong to all of them. Splitting on the comma is what makes those entries
    matchable at all — the whole label was previously used as a single key, and
    `determiner,pronoun` matches nothing any translator ever reports.

    (`strip=True` with no separator is why the comma has no space after it here:
    BeautifulSoup joins the stripped strings directly. Splitting is robust to
    either, so the mangling is left alone rather than papered over.)
    """
    return [part.strip() for part in text.split(",") if part.strip()] or ["other"]


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
    return _node_text(sentence or item)


def _capped(elements, limit, text=None):
    """The text of `elements`, de-duplicated, in order, at most `limit` of them.

    `text` extracts one element's string; the default takes all of it.
    """
    read = text or _node_text
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
        # One entry can head several parts of speech (#228), and its text belongs
        # to each of them — the page does not separate which sense is which.
        poses, defs, exs = _oxford_page_definitions(page)
        for pos in poses:
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
            text = _readable(_node_text(sense).lstrip(":"))
            if text and text not in defs:
                defs.append(text)
            if len(defs) >= MAX_DEFINITIONS:
                break
    return {pos: defs for pos, defs in definitions.items() if defs}


# --- licensed translators (#353) -----------------------------------------
#
# `_google_dictionary()` and `_bing_dictionary()` above are **retired**, not
# deleted. They call endpoints nobody offered us -- the URL Google's own web
# page uses, and the token flow Edge's built-in translator uses -- and #348 is
# both of them being withdrawn on the same day. They stay in the file because
# deleting them would hide that history, and because the response-shaping in
# them is what these fetchers had to match. Nothing selects them: they are not
# in `TRANSLATORS`, so they cannot be chosen or stored.
#
# What every fetcher returns is `{pos: [terms]}` -- the contract
# `lookup_word()` builds cards from, and the one `save_flashcard()` dedupes on
# (#101's word + part of speech).

LANGUAGE_NAMES = {"uk": "Ukrainian", "ru": "Russian"}

# The model for the translation call. Its own constant beside `SPLIT_MODEL`
# rather than a shared one: two features agreeing on a model today are still
# two decisions, which is the reason `textgen.TEXT_MODEL` exists separately as
# well. Haiku follows the precedent both of those set -- a word into two
# languages is not a task that needs a larger model.
TRANSLATE_MODEL = "claude-haiku-4-5-20251001"

# A dozen terms across a few parts of speech, with room for Cyrillic. Bounded
# for the same reason every other model call in this project is.
TRANSLATE_MAX_TOKENS = 700

# The grouping the deck is built on. An enum rather than free text so the
# labels line up with what #228's `POS_SYNONYMS` already matches on, and so a
# model cannot invent a nineteenth part of speech.
TRANSLATE_POS = ("noun", "verb", "adjective", "adverb", "phrase", "other")

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part_of_speech": {"type": "string",
                                       "enum": list(TRANSLATE_POS)},
                    "translations": {"type": "array",
                                     "items": {"type": "string"}},
                },
                "required": ["part_of_speech", "translations"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _claude_dictionary(word, target):
    """Translations grouped by part of speech, from Claude (#353).

    **Structured outputs, not a fenced reply.** `_split_glued_translations()`
    in this same file asks for JSON in prose and strips ``` fences before
    `json.loads()` -- which works, and is a parsing problem that does not need
    to exist. `output_config.format` makes the schema the contract, so a
    malformed answer stops being a failure mode.
    """
    import anthropic

    language = LANGUAGE_NAMES.get(target, target)
    prompt = (
        f"Translate the English word or expression below into {language}, for "
        "a B2-C1 learner's flashcard.\n\n"
        f"    {word}\n\n"
        "Group the translations by the part of speech the English word has in "
        f"that sense, at most {MAX_TRANSLATIONS} per part of speech, most "
        "common first. Give only the parts of speech this word actually has: "
        "one entry is the normal answer and several is unusual. Use 'phrase' "
        "for a multi-word expression and 'other' only when nothing else fits. "
        "Translate the word itself -- no explanations, no transliteration, no "
        "articles and no 'to' before a verb."
    )
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=TRANSLATE_MODEL,
        max_tokens=TRANSLATE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema",
                                  "schema": TRANSLATE_SCHEMA}},
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    data = json.loads(text)

    pos_translations = {}
    for entry in data.get("entries", []):
        pos = (entry.get("part_of_speech") or "other").lower()
        terms = [t.strip() for t in entry.get("translations", [])
                 if isinstance(t, str) and t.strip()][:MAX_TRANSLATIONS]
        if terms:
            pos_translations.setdefault(pos, terms)
    return pos_translations


# The free and paid tiers are different hosts and a key works on one only, so
# the free host is tried first and a 403 moves on rather than failing.
DEEPL_URLS = ("https://api-free.deepl.com/v2/translate",
              "https://api.deepl.com/v2/translate")


def _deepl_dictionary(word, target):
    """DeepL (#353) -- a plain translation, so everything lands under `other`.

    DeepL translates; it does not classify. That is the same shape
    `_google_dictionary()` already fell back to for a word with no dictionary
    entry, and `lookup_word()` has always handled it -- a card with one part of
    speech rather than several. It is also why DeepL is not the default despite
    being the cheapest of the four.
    """
    key = os.environ.get("DEEPL_API_KEY", "")
    refused = None
    for url in DEEPL_URLS:
        resp = requests.post(
            url, headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"text": word, "source_lang": "EN",
                  "target_lang": target.upper()}, timeout=10)
        if resp.status_code == 403:
            refused = resp
            continue
        resp.raise_for_status()
        found = resp.json().get("translations") or []
        plain = ((found[0].get("text") if found else "") or "").strip()
        return ({"other": [plain]}
                if plain and plain.lower() != word.lower() else {})
    if refused is not None:
        refused.raise_for_status()
    return {}


GOOGLE_CLOUD_URL = "https://translation.googleapis.com/language/translate/v2"


def _google_cloud_dictionary(word, target):
    """Google Cloud Translation (#353) -- the licensed version of what
    `_google_dictionary()` was scraping.

    Like DeepL it returns a string rather than a grouping, so the result lands
    under `other`.
    """
    resp = requests.post(
        GOOGLE_CLOUD_URL,
        params={"key": os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")},
        data={"q": word, "source": "en", "target": target, "format": "text"},
        timeout=10)
    resp.raise_for_status()
    found = (resp.json().get("data") or {}).get("translations") or []
    plain = ((found[0].get("translatedText") if found else "") or "").strip()
    return {"other": [plain]} if plain and plain.lower() != word.lower() else {}


MS_API_BASE = "https://api.cognitive.microsofttranslator.com"


def _microsoft_dictionary(word, target):
    """Microsoft Translator (#353) -- the only one of the four whose dictionary
    endpoint returns parts of speech natively.

    The same endpoint and the same `posTag` mapping `_bing_dictionary()` used;
    what changed is the credential. That one minted an anonymous token through
    Edge's browser flow, which is what #348 found withdrawn. This one sends a
    subscription key, which is the supported way to reach the same API.
    """
    def call(path):
        headers = {"Ocp-Apim-Subscription-Key":
                   os.environ.get("MS_TRANSLATOR_KEY", ""),
                   "Content-Type": "application/json"}
        # Required for a regional resource and rejected by a global one, so it
        # is sent only when configured.
        region = os.environ.get("MS_TRANSLATOR_REGION", "").strip()
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        resp = requests.post(f"{MS_API_BASE}/{path}",
                             params={"api-version": "3.0", "from": "en",
                                     "to": target},
                             headers=headers, json=[{"Text": word}], timeout=10)
        resp.raise_for_status()
        return resp.json()

    pos_translations = {}
    for entry in call("dictionary/lookup")[0].get("translations", []):
        pos = BING_POS.get((entry.get("posTag") or "").upper(), "other")
        term = entry.get("displayTarget")
        if not term:
            continue
        terms = pos_translations.setdefault(pos, [])
        if term not in terms and len(terms) < MAX_TRANSLATIONS:
            terms.append(term)

    if not pos_translations:
        plain = ((call("translate")[0]["translations"][0].get("text"))
                 or "").strip()
        if plain and plain.lower() != word.lower():
            pos_translations["other"] = [plain]
    return pos_translations


class Translator(NamedTuple):
    """One way to translate a word, and what a deployment needs to use it.

    The fetcher is held **by name** and resolved through `fetch`, not stored as
    the function object. Storing the object captures it at import time, which
    silently breaks the property the dispatch below has always promised: that
    monkeypatching a single fetcher is picked up. The first version of this
    registry did store the object, and the suite caught it -- every test that
    stubs `_claude_dictionary` was still reaching the real one.
    """
    slug: str
    label: str
    fetch_name: str
    key_env: str
    groups_by_pos: bool

    @property
    def fetch(self):
        return globals()[self.fetch_name]


# **One declaration.** The Settings panel, the availability check and the
# dispatch all read this, so adding a fifth provider is one entry rather than
# one entry and three edits -- the rule `games.ACTIVITIES` follows for the
# activities (#253).
#
# Order is the order the panel offers them, and Claude leads because it is the
# only one that both licenses this use and returns the part-of-speech grouping
# the deck is built on.
TRANSLATORS = (
    Translator("claude", "Claude", "_claude_dictionary",
               "ANTHROPIC_API_KEY", True),
    Translator("microsoft", "Microsoft Translator", "_microsoft_dictionary",
               "MS_TRANSLATOR_KEY", True),
    Translator("deepl", "DeepL", "_deepl_dictionary",
               "DEEPL_API_KEY", False),
    Translator("google_cloud", "Google Cloud Translation",
               "_google_cloud_dictionary", "GOOGLE_TRANSLATE_API_KEY", False),
)

TRANSLATOR_SLUGS = tuple(t.slug for t in TRANSLATORS)


def available_translators():
    """The providers this deployment is configured for, in panel order.

    Read from the environment at **call time**, never captured at import: the
    same rule `_generation_available()` follows for #237, and it is what lets a
    key be added without a code change and a test set one without reloading a
    module.
    """
    return tuple(t for t in TRANSLATORS
                 if os.environ.get(t.key_env, "").strip())


def translator(slug):
    """The named provider, or None."""
    return next((t for t in TRANSLATORS if t.slug == slug), None)


# Resolved at call time (not captured in a module-level dict) so tests can
# monkeypatch a single fetcher and the dispatch picks the replacement up.
def _translator_backend(translator_slug):
    """The fetcher for a chosen provider, falling back to what is configured.

    A stored choice whose key has since been removed falls through to the first
    available provider rather than failing every lookup -- and when nothing is
    configured this returns **None**, which `lookup_word()` reads as "no
    translator" and answers with #349's dictionary-only card.
    """
    chosen = translator(translator_slug)
    available = available_translators()
    if chosen and chosen in available:
        return chosen.fetch
    return available[0].fetch if available else None


def _merriam_webster_entry(word):
    """Merriam-Webster in the entry shape — definitions, and no examples (#225).

    It does have examples on the page; extracting them is not this ticket's, and
    it is unreachable from PythonAnywhere anyway. Returning an empty second half
    keeps every dictionary backend the same shape, which is what lets
    `lookup_word()` have one seam instead of a branch per provider.
    """
    return _fetch_merriam_webster_definitions(word), {}


# Two providers, two names for the same part of speech (#228). Applied to
# **both** sides, so it does not matter which one uses which word — and only for
# matching: a card keeps the label its translator gave it, because that is what
# the learner sees on it.
#
# Built from what the providers actually emit over a 28-word survey covering
# content words, modals and function words:
#
#   Google  noun adjective verb adverb "auxiliary verb" pronoun preposition
#           conjunction particle interjection
#   Oxford  noun verb adjective adverb preposition pronoun conjunction
#           determiner exclamation "modal verb" other abbreviation
#
# Seven labels already agreed. These are the two pairs that did not, and each is
# one provider's word for the other's:
POS_SYNONYMS = {
    # Google's name for `must`, `can`, `should`. Oxford says "modal verb" and
    # calls `be` and `have` plain verbs, so nothing collides here.
    "auxiliary verb": "modal verb",
    # Google's name for `hello`, `ouch`. Oxford says "exclamation".
    "interjection": "exclamation",
}


def _pos_key(label):
    """The label to match a part of speech on, which is not what it is called."""
    return POS_SYNONYMS.get(label, label)


def _attach_dictionary_text(cards, definitions, examples):
    """Put each dictionary entry's explanation and examples on the right card.

    Matching used to be `if pos in cards` — exact string equality — which threw
    away text that had already been fetched whenever the two providers named the
    same part of speech differently. `must` was the demonstration: Google reports
    an *auxiliary verb*, Oxford a *modal verb*, so Oxford's definition was
    discarded **and** Google's card was left blank. One mismatch, two losses.

    **No card is created or removed here** (#228). A translation is enough to
    keep a card, so a part of speech the dictionary has nothing for keeps its
    translations and simply carries no English text.
    """
    # canonical label -> the card to put that text on. First wins, so a card
    # whose own label matches exactly is never displaced by a synonym.
    index = {}
    for pos in cards:
        index.setdefault(_pos_key(pos), pos)

    unplaced = []
    for pos in sorted(set(definitions) | set(examples)):
        target = index.get(_pos_key(pos))
        if target is None:
            unplaced.append(pos)
            continue
        if pos in definitions:
            cards[target]["explanation_en"] = "; ".join(definitions[pos])
        # A list, not a joined string: `examples_en` is one of utils.LIST_FIELDS
        # and is stored as JSON, so the card page shows separate sentences.
        if pos in examples:
            cards[target]["examples_en"] = examples[pos]

    # The `other` card can never match anything, by construction: it is what
    # _google_dictionary() falls back to when Google has no dictionary entry, so
    # it is not a part of speech at all. One unplaced dictionary entry is
    # therefore unambiguously its text — `overbook` is the case. More than one
    # and there is no way to tell which, so it keeps its translations only.
    if "other" in cards and len(unplaced) == 1:
        pos = unplaced[0]
        if pos in definitions:
            cards["other"]["explanation_en"] = "; ".join(definitions[pos])
        if pos in examples:
            cards["other"]["examples_en"] = examples[pos]


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
        _claude_dictionary: "claude",
        _microsoft_dictionary: "microsoft",
        _deepl_dictionary: "deepl",
        _google_cloud_dictionary: "google_cloud",
        # Retired (#353), and still named here: a log line from before the
        # change should keep reading as the provider that wrote it.
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
    # The chosen provider first, then the rest of what is configured (#353).
    # The old fallback was hard-coded to Google, which is exactly the coupling
    # that made one provider's withdrawal an outage; a list means a second
    # configured provider covers the first, and no list means no translator --
    # which is a state the app now survives, see below.
    chosen = _translator_backend(translator)
    order = ([chosen] + [t.fetch for t in available_translators()
                         if t.fetch is not chosen]) if chosen else []
    cards = {}  # pos -> entry dict

    for key, code in GOOGLE_LANGS.items():
        pos_translations = {}
        for attempt, fetch_translations in enumerate(order):
            primary = _provider_name(fetch_translations)
            error = None
            with applog.Timer() as timer:
                try:
                    pos_translations = fetch_translations(word, code)
                except (requests.RequestException, ValueError, KeyError,
                        IndexError, RuntimeError) as e:
                    # Wider than the old pair on purpose: these are four
                    # different providers' response shapes, and a key that has
                    # moved must degrade to the next one rather than 500.
                    pos_translations = {}
                    error = e
            applog.translations_fetched(
                word, primary, code, len(pos_translations), timer.ms,
                error=error,
                **({"fallback_from": _provider_name(order[0])}
                   if attempt else {}))
            if pos_translations:
                break
        for pos, terms in pos_translations.items():
            entry = cards.setdefault(pos, {"word": word, "pos": pos, "topic": topic})
            entry[f"translation_{key}"] = ", ".join(terms)

    # Same rule as the Reverso parser: the untagged catch-all card is kept
    # only when no part of speech was identified at all.
    if len(cards) > 1:
        cards.pop("other", None)

    # The raise used to be here, *before* the dictionary was consulted at all,
    # so a translator outage threw away an explanation the app had not yet
    # asked for (#348 was exactly that: both translators down while Oxford
    # answered perfectly). It now happens below, once both halves are in.
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
    # #349. No translator answered, but the dictionary did: build the cards
    # from the dictionary's parts of speech instead, and leave the translation
    # fields empty.
    #
    # A deliberate inversion of the usual rule -- a card is normally created
    # per part of speech the *translator* found and takes its text from the
    # part of speech the *dictionary* found (#228). That stays true whenever a
    # translator answers. This is the fallback for when none does, and an
    # English explanation with examples is most of a card's value to a B2-C1
    # learner: refusing to save one because a different provider is rate-
    # limited throws away work already done.
    if not cards:
        for pos in sorted(set(definitions) | set(examples)):
            cards[pos] = {"word": word, "pos": pos, "topic": topic}
        # Same rule the translator half applies: the untagged catch-all is kept
        # only when nothing else was identified.
        if len(cards) > 1:
            cards.pop("other", None)
        if cards:
            applog.lookup_degraded(word, len(cards), dictionary)

    if not cards:
        # Both halves empty is still a failure -- there is nothing to make a
        # card from. The message names *why* rather than blaming the word:
        # "No translations found for 'scholar'" reads as "that word does not
        # exist", and it sent #348's investigation at the wrong provider.
        applog.lookup_failed(word, "no translations and no definitions")
        raise ValueError(
            f"Could not look up '{word}': no translation service answered, "
            f"and {dictionary} has no entry for it either.")

    _attach_dictionary_text(cards, definitions, examples)

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
        text = _node_text(node)
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
        text = _node_text(node)
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
            sense[field] = _readable(text) if field == "explanation" else text

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
        # The line as the learner will read it (#359). The header below is
        # parsed from the raw text instead: it matches on exact substrings
        # rather than being shown to anyone.
        text = _node_text(p)
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
