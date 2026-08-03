import json
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

import applog

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


def claim_anonymous_message(daily_limit):
    """
    Count one anonymous message against today's ceiling (issue #164).

    Returns (allowed, used_today). The increment and the check happen in one
    statement, so two workers can't both slip past the last message: the row
    only advances while it is under the limit, and `ROW_COUNT()` says whether
    this call was the one that got it.

    A `daily_limit` of 0 or less means "no ceiling" and never touches the
    database.
    """
    if not daily_limit or daily_limit <= 0:
        return True, 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO anonymous_usage (day, messages) VALUES (CURDATE(), 1)
            ON DUPLICATE KEY UPDATE
                messages = IF(messages < %s, messages + 1, messages)
            """,
            (daily_limit,),
        )
        # Read straight after the write, before anything else touches the
        # cursor: 1 = inserted, 2 = updated and changed, 0 = the IF() held it
        # back because the ceiling was already reached. That 0 is the only way
        # to tell "I took the last slot" from "someone else did".
        advanced = cursor.rowcount != 0
        conn.commit()
        cursor.execute(
            "SELECT messages FROM anonymous_usage WHERE day = CURDATE()")
        row = cursor.fetchone()
        cursor.close()
        return advanced, (row[0] if row else 0)
    finally:
        conn.close()


def upsert_user(google_sub, email, display_name=None, given_name=None,
                family_name=None):
    """
    Record a Google sign-in (issue #148) and return (user_id, preferred_name).

    Keyed on `google_sub` — Google's OIDC subject, which is unique per account
    and never changes. An email change therefore updates the existing row
    instead of creating a second one and orphaning the user's cards.

    `preferred_name` is deliberately never written here: it is the user's own
    choice of what Mykola calls them, not something Google supplies, so a
    sign-in must not overwrite it. It is read back so the caller can put it in
    the session without a second query.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # LAST_INSERT_ID(id) makes lastrowid report the *existing* row's id on
        # the update branch — without it, an update leaves lastrowid at 0.
        cursor.execute(
            """
            INSERT INTO users (google_sub, email, display_name, given_name,
                               family_name, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                email = VALUES(email),
                display_name = VALUES(display_name),
                given_name = VALUES(given_name),
                family_name = VALUES(family_name),
                last_seen_at = NOW()
            """,
            (google_sub, email, display_name, given_name, family_name),
        )
        user_id = cursor.lastrowid
        cursor.execute("SELECT preferred_name FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        return user_id, (row[0] if row else None)
    finally:
        conn.close()


def set_preferred_name(user_id, name):
    """Store what Mykola should call this user, or clear it (ai_agent#62).

    `name` of None clears the column back to NULL, which means "use the name
    from the account". Storing the literal first name instead would look the
    same today and then shadow a later change to the Google name for ever.

    Returns True when a row was updated. False means no such account, which
    the caller reports rather than treating as success.
    """
    if user_id is None:
        return False
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET preferred_name = %s WHERE id = %s",
                       (name, user_id))
        updated = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        return updated
    finally:
        conn.close()


def get_user_block(user_id):
    """When and why an account was blocked (issue #126), or None.

    Returns (blocked_at, blocked_reason) for a blocked account and None for
    an account in good standing — so the caller reads it as a plain truth
    value and never has to compare timestamps.

    Read live rather than stamped into the session at sign-in: a block has to
    take effect on the blocked person's *next request*, not whenever they
    happen to sign in again, and a session cookie lasts 30 days.
    """
    if user_id is None:
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT blocked_at, blocked_reason FROM users WHERE id = %s",
            (user_id,))
        row = cursor.fetchone()
        cursor.close()
        if row is None or row[0] is None:
            return None
        return row[0], row[1]
    finally:
        conn.close()


def set_user_blocked(email, blocked, reason=None):
    """Block or unblock the account with this email (issue #126).

    Returns (user_id, was_blocked) for the account it changed, or None when
    the email matches no account — so a typo reports a miss instead of
    reporting success on nothing. Matched exactly and case-insensitively:
    never a prefix or a LIKE, which could catch a bystander.

    Unblocking clears the reason as well. It is a note about a block that is
    over, and leaving it behind would make the next reader think the account
    is still blocked.

    Logged here rather than by the caller — unlike save_flashcard, whose
    logging lives in app._save_and_log. There is no request behind this and
    only one caller today, so putting the line where the change happens is
    what makes an unlogged block impossible.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, blocked_at FROM users WHERE LOWER(email) = LOWER(%s)",
            ((email or "").strip(),))
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return None
        user_id, blocked_at = row
        if blocked:
            cursor.execute(
                "UPDATE users SET blocked_at = NOW(), blocked_reason = %s "
                "WHERE id = %s", (reason, user_id))
        else:
            cursor.execute(
                "UPDATE users SET blocked_at = NULL, blocked_reason = NULL "
                "WHERE id = %s", (user_id,))
        conn.commit()
        cursor.close()
        was_blocked = blocked_at is not None
        if blocked:
            # Re-blocking an already-blocked account is still an event: the
            # timestamp moves and the reason may be new.
            applog.user_blocked(user_id, email, reason=reason)
        elif was_blocked:
            # Unblocking one that was not blocked changed nothing, and a line
            # for it would make the log over-count the blocks that were lifted.
            applog.user_unblocked(user_id, email)
        return user_id, was_blocked
    finally:
        conn.close()


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


def save_flashcard(entry, added_by_user_id=None):
    """
    Insert a flashcard entry into the `flashcards` table, unless a card with
    the same word and part of speech already exists anywhere in the database
    (issue #101) — repeated lookups used to pile up duplicate rows.

    Returns the new row id, or None when the card was skipped as a duplicate
    (callers use that to tell the user the word is already present).
    Ukrainian and Russian fields are optional; missing keys are stored as NULL.

    `added_by_user_id` (issue #89) records who saved the card; None means an
    anonymous visitor. It is a separate argument rather than a member of
    FLASHCARD_FIELDS on purpose: `entry` is built from submitted form data, so
    reading the id out of it would let a browser attribute a card to another
    user. Callers must take it from the server-side session.
    """
    def serialize(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    columns = FLASHCARD_FIELDS + ("added_by_user_id",)
    values = tuple(serialize(entry.get(field)) for field in FLASHCARD_FIELDS)
    values += (added_by_user_id,)

    query = f"""
        INSERT INTO flashcards ({", ".join(columns)})
        VALUES ({", ".join(["%s"] * len(columns))})
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


# What an edit may change (issue #176). `topic` is deliberately absent: moving
# a card between topics has different rules and its own ticket (#177), and
# `added_by_user_id` is never editable at all — it is the answer to "whose card
# is this?", which is the question the edit permission itself depends on.
EDITABLE_FIELDS = tuple(f for f in FLASHCARD_FIELDS if f != "topic")


def find_duplicate(word, pos, exclude_id=None):
    """The card that already holds this word + part of speech, or None (#101).

    Returns (id, added_by_user_id) so the caller can say something useful — in
    particular whether the blocking card is even visible to this visitor
    (#186), which is not obvious when #127's filter is on.

    `exclude_id` leaves one card out of the search: an edit must not find
    *itself* and refuse to save (#176).

    Matching is the same as save_flashcard's, and for the same reasons: the
    column collation makes it case-insensitive, and `<=>` is NULL-safe so two
    pos-less cards count as duplicates of each other.
    """
    sql = "SELECT id, added_by_user_id FROM flashcards WHERE word = %s AND pos <=> %s"
    params = [word, pos]
    if exclude_id is not None:
        sql += " AND id != %s"
        params.append(exclude_id)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql + " LIMIT 1", tuple(params))
        row = cursor.fetchone()
        cursor.close()
        return (row[0], row[1]) if row else None
    finally:
        conn.close()


def update_flashcard(card_id, entry, owner_id=None, admin=False):
    """Change a saved card's content (issue #176).

    Returns `(outcome, detail)`:

    - `("updated", [changed field names])`
    - `("unchanged", [])`  — the submitted values match what is stored
    - `("duplicate", (other_id, word, pos))` — word+pos already taken
    - `("denied", None)`   — the card is not this visitor's to edit
    - `("missing", None)`  — no such card

    Ownership works exactly as `delete_flashcard()` does, and for the same
    reason: the write is **one conditional statement**, so there is no gap
    between deciding the card is the caller's and changing it. `=` never
    matches NULL, so unowned cards (pre-#89) are admin-only.

    `created_at` is left alone — it is the card's age, not its last touch.

    "unchanged" is distinct from "updated" on purpose: an edit that changed
    nothing should not write an EDIT line into the action log, which counts
    events rather than attempts (the lesson from #126's unblock logging).

    Only the fields **present in `entry`** are touched. A missing key means
    "leave this alone", which is different from a key holding None ("clear
    it"). That distinction is what lets the editor omit a language the visitor
    has hidden (#46/#79/#111) without wiping it from the card — hiding a
    language has always been visual only, and an editor that silently emptied
    the hidden half would make it destructive.
    """
    def serialize(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT id, {', '.join(EDITABLE_FIELDS)} FROM flashcards WHERE id = %s",
            (card_id,))
        current = cursor.fetchone()
        if current is None:
            cursor.close()
            return "missing", None

        new = {f: serialize(entry[f]) for f in EDITABLE_FIELDS if f in entry}

        # A rename can collide with an existing card, which is the whole point
        # of #101 — but the card being edited must not count as its own
        # duplicate. Checked before the write so nothing is half-applied, and
        # against the values the card will *end up* with, since either half of
        # the word+pos pair may be the one left untouched.
        word = new.get("word", current["word"])
        pos = new.get("pos", current["pos"])
        if word != current["word"] or pos != current["pos"]:
            cursor.execute(
                "SELECT id FROM flashcards "
                "WHERE word = %s AND pos <=> %s AND id != %s LIMIT 1",
                (word, pos, card_id))
            clash = cursor.fetchone()
            if clash is not None:
                cursor.close()
                return "duplicate", (clash["id"], word, pos)

        changed = [f for f in EDITABLE_FIELDS
                   if f in new and new[f] != current[f]]
        if not changed:
            cursor.close()
            return "unchanged", []

        assignments = ", ".join(f"{f} = %s" for f in changed)
        values = tuple(new[f] for f in changed)
        if admin:
            cursor.execute(
                f"UPDATE flashcards SET {assignments} WHERE id = %s",
                values + (card_id,))
        else:
            cursor.execute(
                f"UPDATE flashcards SET {assignments} "
                "WHERE id = %s AND added_by_user_id = %s",
                values + (card_id, owner_id))
        if cursor.rowcount == 0:
            cursor.close()
            return "denied", None
        conn.commit()
        cursor.close()
        return "updated", changed
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


def resolve_user_cards(user_id, keep_cards=True) -> int:
    """Settle a departing user's cards (#165) and return how many were touched.

    ``keep_cards`` leaves them in place with no owner — they become community
    property, readable by everyone and deletable only by the admin (#162).
    Otherwise they are removed with the account.

    This has to happen **before** the users row goes: `added_by_user_id` is
    ON DELETE RESTRICT (#89/#165), so a deletion that skipped this step would
    fail loudly on the foreign key rather than quietly cascading or
    anonymising against the user's wishes.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if keep_cards:
            cursor.execute(
                "UPDATE flashcards SET added_by_user_id = NULL "
                "WHERE added_by_user_id = %s", (user_id,))
        else:
            cursor.execute(
                "DELETE FROM flashcards WHERE added_by_user_id = %s", (user_id,))
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        return affected
    finally:
        conn.close()


def delete_user(user_id) -> bool:
    """Delete a users row; True if it was there. Call this LAST (#165).

    While the row exists the account is still coherent and the deletion can be
    retried; once it is gone, any files or cards left behind are orphaned with
    nothing to attribute them to.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        return deleted
    finally:
        conn.close()


def delete_flashcard(card_id, owner_id=None, admin=False):
    """
    Delete a flashcard by id, subject to ownership (issue #162).

    Returns `(word, outcome)`, where outcome is one of:

    - `"deleted"` — the row is gone; `word` is what it held.
    - `"denied"`  — the card exists but does not belong to this user.
    - `"missing"` — no such card; `word` is None.

    The removal itself is **one conditional statement**, so there is no gap
    between deciding the card is the caller's and taking it. The word is read
    first only so the caller has something to say afterwards; a card that
    changes hands in between simply affects no rows and reads as denied.

    `=` never matches NULL, so cards with no owner — everything saved before
    #89, and anything saved anonymously — are deletable only by the admin.
    That is deliberate: they are nobody's to reclaim.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM flashcards WHERE id = %s", (card_id,))
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return None, "missing"
        word = row[0]
        if admin:
            cursor.execute("DELETE FROM flashcards WHERE id = %s", (card_id,))
        else:
            cursor.execute(
                "DELETE FROM flashcards WHERE id = %s AND added_by_user_id = %s",
                (card_id, owner_id),
            )
        if cursor.rowcount == 0:
            cursor.close()
            return word, "denied"
        conn.commit()
        cursor.close()
        return word, "deleted"
    finally:
        conn.close()


def _owner_clause(owner_id):
    """SQL and parameters restricting a card query to one owner (issue #127).

    `owner_id` of None means "every card", which is the shared deck the site
    has always shown — not "cards belonging to nobody". The distinction
    matters: `added_by_user_id = NULL` is never true in SQL, so an
    accidentally-None owner would silently hide the entire deck instead of
    showing it, and the page would look broken rather than filtered.

    Equality is also what correctly excludes the unowned cards (NULL: saved
    before #89) from an individual view. They are nobody's, so they are not
    yours.
    """
    if owner_id is None:
        return "", ()
    return " AND added_by_user_id = %s", (owner_id,)


def get_topics(owner_id=None):
    """
    Return all topics that have flashcards, as (topic, card_count) tuples
    sorted by topic name.

    With `owner_id`, only that user's cards are counted (#127), so a topic
    made entirely of other people's cards disappears from the list rather
    than opening empty.
    """
    clause, params = _owner_clause(owner_id)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic, COUNT(*) FROM flashcards "
            "WHERE topic IS NOT NULL AND topic != ''" + clause +
            " GROUP BY topic ORDER BY topic",
            params,
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


def get_flashcards_by_topic(topic, owner_id=None):
    """
    Fetch all flashcards for the given topic as a list of dictionaries.
    The examples_* fields are deserialized back into lists.

    With `owner_id`, only that user's cards come back (#127). None means
    every card, as it always has — see _owner_clause().
    """
    clause, params = _owner_clause(owner_id)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM flashcards WHERE topic = %s" + clause +
            " ORDER BY word",
            (topic,) + params,
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    for row in rows:
        for field in LIST_FIELDS:
            row[field] = _to_list(row.get(field))
    return rows
