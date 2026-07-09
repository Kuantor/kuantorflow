"""
Parsers that turn external sources (Reverso Context lookups, OneNote .mht
exports) into flashcard entry dictionaries ready for utils.save_flashcard().
"""

import email
from email import policy
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

REVERSO_URL = "https://context.reverso.net/translation/english-{lang}/{word}"
REVERSO_LANGS = {"ukr": "ukrainian", "rus": "russian"}

# Reverso blocks requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

MAX_TRANSLATIONS = 3
MAX_EXAMPLES = 3


def _fetch_reverso(word, lang):
    """
    Fetch one english-<lang> Reverso Context page and extract the top
    translations plus parallel example sentences (English / target language).
    Returns (translations, examples_en, examples_target).
    """
    url = REVERSO_URL.format(lang=lang, word=quote(word))
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    translations = [
        el.get_text(strip=True)
        for el in soup.select("#translations-content .translation .display-term")
    ]
    if not translations:
        translations = [
            el.get_text(strip=True) for el in soup.select("a.translation")
        ]
    translations = [t for t in translations if t][:MAX_TRANSLATIONS]

    examples_en, examples_target = [], []
    for example in soup.select("div.example")[:MAX_EXAMPLES]:
        src = example.select_one(".src .text")
        trg = example.select_one(".trg .text")
        if src and trg:
            examples_en.append(src.get_text(" ", strip=True))
            examples_target.append(trg.get_text(" ", strip=True))

    return translations, examples_en, examples_target


def parse_reverso_word(word, topic=None):
    """
    Look up a word on Reverso Context (English→Ukrainian and English→Russian)
    and build a flashcard entry dictionary.
    """
    entry = {"word": word, "topic": topic}

    for key, lang in REVERSO_LANGS.items():
        try:
            translations, examples_en, examples_target = _fetch_reverso(word, lang)
        except requests.RequestException:
            continue  # keep the other language even if one lookup fails
        if translations:
            entry[f"translation_{key}"] = ", ".join(translations)
        if examples_target:
            entry[f"examples_{key}"] = examples_target
        if examples_en and "examples_en" not in entry:
            entry["examples_en"] = examples_en

    if len(entry) == 2:  # nothing beyond word/topic was found
        raise ValueError(f"No Reverso results found for '{word}'")
    return entry


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
