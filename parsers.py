"""
Parsers that turn external sources (Reverso Context lookups, OneNote .mht
exports) into flashcard entry dictionaries ready for utils.save_flashcard().
"""

import email
import re
from email import policy
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

REVERSO_URL = "https://context.reverso.net/translation/english-{lang}/{word}"
REVERSO_LANGS = {"ukr": "ukrainian", "rus": "russian"}
DEFINITION_URL = "https://dictionary.reverso.net/english-definition/{word}"

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


def parse_reverso_word(word, topic=None):
    """
    Look up a word on Reverso Context (English→Ukrainian and English→Russian)
    and build flashcard entries — one per part of speech found, so e.g.
    'run' produces separate verb and noun cards. English definitions from
    Reverso's dictionary are attached to the matching cards.
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


def parse_mht_file(data, topic=None):
    """
    Parse an .mht (MIME HTML, e.g. OneNote export) file and extract flashcard
    entries from lines shaped like 'word — explanation'.

    `data` is the raw file content as bytes. Returns a list of entry dicts.
    """
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

    soup = BeautifulSoup(html, "lxml")
    entries = []
    seen_words = set()
    for node in soup.find_all(["p", "li", "td"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        entry = _entry_from_line(text, topic)
        if entry and entry["word"].lower() not in seen_words:
            seen_words.add(entry["word"].lower())
            entries.append(entry)
    return entries
