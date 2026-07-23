import json
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# Load settings from a .env file next to this module (gitignored).
# Values already present in the environment take precedence.
load_dotenv(Path(__file__).with_name(".env"))


def get_db_connection():
    """
    Connect to MySQL using DB_* environment variables (see .env.example).
    DB_HOST and DB_NAME default to the PythonAnywhere shape derived from
    DB_USER; DB_PASSWORD has no default and must always be set.
    """
    user = os.environ.get("DB_USER", "kuantorflow")
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "DB_PASSWORD is not set — copy .env.example to .env and fill it in"
        )
    conn = mysql.connector.connect(
        user=user,
        password=password,
        host=os.environ.get(
            "DB_HOST", f"{user}.mysql.pythonanywhere-services.com"
        ),
        database=os.environ.get("DB_NAME", f"{user}$default"),
        connection_timeout=5,
    )
    return conn


FLASHCARD_FIELDS = (
    "word",
    "pos",
    "explanation_en",
    "examples_en",
    "translation_ukr",
    "examples_ukr",
    "translation_rus",
    "examples_rus",
    "topic",
)


def save_flashcard(entry):
    """
    Insert a flashcard entry into the `flashcards` table, unless a card with
    the same word and part of speech already exists anywhere in the database
    (issue #101) — repeated lookups used to pile up duplicate rows.

    Returns the new row id, or None when the card was skipped as a duplicate
    (callers use that to tell the user the word is already present).
    Ukrainian and Russian fields are optional; missing keys are stored as NULL.
    """
    def serialize(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    values = tuple(serialize(entry.get(field)) for field in FLASHCARD_FIELDS)

    query = f"""
        INSERT INTO flashcards ({", ".join(FLASHCARD_FIELDS)})
        VALUES ({", ".join(["%s"] * len(FLASHCARD_FIELDS))})
    """

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Word matching follows the column's collation (case-insensitive by
        # default); the NULL-safe <=> lets pos-less cards (e.g. .mht imports)
        # be deduplicated too, which a UNIQUE index could not do — MySQL
        # treats NULLs in a unique key as distinct.
        cursor.execute(
            "SELECT id FROM flashcards WHERE word = %s AND pos <=> %s LIMIT 1",
            (entry.get("word"), entry.get("pos")),
        )
        if cursor.fetchone() is not None:
            cursor.close()
            return None
        cursor.execute(query, values)
        conn.commit()
        row_id = cursor.lastrowid
        cursor.close()
        return row_id
    finally:
        conn.close()


def flashcard_word_exists(word):
    """
    True if any flashcard already has this word (issue #145) — used to warn on
    lookup, before the review dialog. Matching follows the column collation
    (case-insensitive by default), so 'Run' and 'run' count as the same word.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM flashcards WHERE word = %s LIMIT 1", (word,))
        exists = cursor.fetchone() is not None
        cursor.close()
        return exists
    finally:
        conn.close()


def delete_flashcard(card_id):
    """
    Delete a flashcard by id.
    Returns the deleted card's word, or None if no such card exists.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM flashcards WHERE id = %s", (card_id,))
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return None
        cursor.execute("DELETE FROM flashcards WHERE id = %s", (card_id,))
        conn.commit()
        cursor.close()
        return row[0]
    finally:
        conn.close()


def get_topics():
    """
    Return all topics that have flashcards, as (topic, card_count) tuples
    sorted by topic name.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic, COUNT(*) FROM flashcards "
            "WHERE topic IS NOT NULL AND topic != '' "
            "GROUP BY topic ORDER BY topic"
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return rows


LIST_FIELDS = ("examples_en", "examples_ukr", "examples_rus")


def _to_list(value):
    """
    Convert a stored text value back into a list.
    Handles JSON arrays (how save_flashcard stores lists), plain strings
    with one example per line, and NULL (returned as an empty list).
    """
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [value]


def get_flashcards_by_topic(topic):
    """
    Fetch all flashcards for the given topic as a list of dictionaries.
    The examples_* fields are deserialized back into lists.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM flashcards WHERE topic = %s ORDER BY word",
            (topic,),
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    for row in rows:
        for field in LIST_FIELDS:
            row[field] = _to_list(row.get(field))
    return rows
