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


# The row in text_generation_usage that counts everybody rather than one
# account. See schema.sql for why it is 0 and not NULL.
ALL_ACCOUNTS = 0


def _claim_one_text(cursor, user_id, limit):
    """Take a slot on one text_generation_usage row. True if this call got it.

    The same single statement `claim_anonymous_message()` uses, for the same
    reason: the row only advances while it is under the limit, and `ROW_COUNT()`
    says whether *this* request was the one that advanced it, so two workers
    cannot both slip past the last slot.
    """
    cursor.execute(
        """
        INSERT INTO text_generation_usage (day, user_id, texts)
        VALUES (CURDATE(), %s, 1)
        ON DUPLICATE KEY UPDATE texts = IF(texts < %s, texts + 1, texts)
        """,
        (user_id, limit),
    )
    return cursor.rowcount != 0


def claim_text_generation(user_id, user_limit, daily_limit):
    """Count one generated text against both of its ceilings (issue #237).

    Returns `(allowed, scope, used)` — `scope` is None when the text may go
    ahead, or "user" / "daily" naming the ceiling that refused it, so the caller
    can say which one plainly rather than showing one message for both. `used`
    is the count on the row that decided.

    `user_id` is None for an anonymous visitor, who has no per-account row: they
    are held by a session counter instead (#164's shape), and their texts still
    count towards the daily ceiling, which is the one actually bounding the
    bill. A limit of 0 or less means "no ceiling".

    **The per-account claim goes first, and the order is deliberate.** Two rows
    cannot be claimed in one atomic statement, so one of them can be taken by a
    generation the other then refuses. Claiming the account first means a
    learner can spend one of their ten on a day the whole site is exhausted;
    the reverse burns a site-wide slot for somebody who was already over their
    own limit, which is the worse of the two. Neither matters at a third of a
    cent — but it is decided rather than accidental.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for scope, row, limit in (("user", user_id, user_limit),
                                  ("daily", ALL_ACCOUNTS, daily_limit)):
            if row is None or not limit or limit <= 0:
                continue
            # Read rowcount straight after the write, before anything else
            # touches the cursor: 0 means the IF() held the row back because
            # the ceiling was already reached.
            if not _claim_one_text(cursor, row, limit):
                conn.commit()
                cursor.execute(
                    "SELECT texts FROM text_generation_usage "
                    "WHERE day = CURDATE() AND user_id = %s", (row,))
                found = cursor.fetchone()
                cursor.close()
                return False, scope, (found[0] if found else 0)
        conn.commit()
        cursor.execute(
            "SELECT texts FROM text_generation_usage "
            "WHERE day = CURDATE() AND user_id = %s",
            (ALL_ACCOUNTS if user_id is None else user_id,))
        found = cursor.fetchone()
        cursor.close()
        return True, None, (found[0] if found else 0)
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
    # Which dictionary wrote the explanation (#390), and only ever written
    # beside one -- `parsers._attach_dictionary_text()` stamps it on the cards
    # it gives text to and on no others. Wiktionary's licence asks for a
    # credit, and this is what a card knows to credit.
    "explanation_source",
    "examples_en",
    "translation_ukr",
    "examples_ukr",
    "translation_rus",
    "examples_rus",
    "topic",
)


# Where a brand-new topic goes (#215). 'Other' is the bucket the migration put
# every pre-existing topic in, and a topic invented by a lookup, an import or
# Mykola belongs there for the same reason: nobody has decided its place in a
# curriculum. Keeping this true at the point of creation is what lets readers
# treat topics.section_id as always set.
DEFAULT_SECTION = "Other"


def _default_section_id(cursor):
    """The id of the section a new topic is filed under, or None (#215).

    None only on a database where #215's `topic_sections rows` step has not run
    — a deploy caught between applying the schema and reloading, in practice.
    Saving a card must not fail for that, so the topic is created with no
    section and the backfill adopts it on the next `apply_schema.py`.

    A missing *table*, unlike a missing row, is left to raise: that is the same
    half-deployed database a missing column would fail on, and #180's whole
    point is that it should be loud.
    """
    cursor.execute(
        "SELECT id FROM topic_sections WHERE name = %s", (DEFAULT_SECTION,))
    row = cursor.fetchone()
    return row[0] if row is not None else None


def _get_or_create_topic(cursor, name, created_by_user_id=None):
    """The id of the topic called `name`, creating the row if it is new (#207).

    Returns `(topic_id, canonical_name, created)`. A blank or missing name gives
    `(None, None, False)` — "no topic" is a state a card is allowed to be in, and
    it must not become a topic *named* '', which the UNIQUE key would happily
    keep exactly once.

    The canonical name is the one **stored on the topics row**, which is not
    always the one passed in: names match case-insensitively, so a card saved
    under 'environment AND climate' belongs to the existing 'Environment and
    climate'. Callers write that back into `flashcards.topic`, so the string
    column and the row it points at never disagree about spelling — which
    matters while ai_agent is still reading the string.

    This is the only place a topic name becomes an id, and it is deliberately
    reached from inside `save_flashcard()` and `move_flashcard()` rather than
    from their callers. Everything upstream speaks names: `lookup_word()` and
    all six note parsers stamp a `topic` string into the entry, the review popup
    posts it as a form field, #177's move dialog promises that an unknown name
    creates the topic, and Mykola's tool schema has the *model* infer one. Ids
    would have to be threaded through all of that, and the agent would have to
    learn about database keys it has no business knowing.

    `created_by_user_id` is attribution, and comes from the caller's session id
    — never from `entry`, for the reason `save_flashcard()` already gives about
    `added_by_user_id`: a value read out of submitted data lets a browser
    attribute someone else's work.

    Matching a name follows the column collation, so 'Work' finds 'work': the
    same rule the old GROUP BY applied when it displayed them as one topic.

    Since #382 a name can match **two** rows -- the public one and a private one
    belonging to whoever is asking -- and the caller's own wins. That is the
    whole of what private topics cost this function: a learner with a private
    'Work' files into it, everybody else files into the public 'Work', and
    neither can see the other's. An anonymous caller has no namespace of their
    own, so `namespace = NULL` matches nothing and they can only ever reach the
    public row.
    """
    name = (name or "").strip()
    if not name:
        return None, None, False
    cursor.execute(
        "SELECT id, name FROM topics "
        "WHERE name = %s AND (namespace = 0 OR namespace = %s) "
        "ORDER BY (namespace = %s) DESC LIMIT 1",
        (name, created_by_user_id, created_by_user_id),
    )
    row = cursor.fetchone()
    if row is not None:
        return row[0], row[1], False
    # Two workers can reach this line for the same new topic at once — the
    # notes upload and a chat save, say. LAST_INSERT_ID(id) is the same idiom
    # upsert_user() uses: the loser of the race reads the winner's id back
    # instead of failing on the unique key.
    # Resolved here rather than at import: the section rows are data, so a
    # long-lived worker started before the migration would otherwise cache a
    # None it never re-checks. `position` takes its column default of 0, which
    # is what puts a new topic in 'Other' in alphabetical order (#215).
    section_id = _default_section_id(cursor)
    cursor.execute(
        "INSERT INTO topics (name, created_by_user_id, section_id) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)",
        (name, created_by_user_id, section_id),
    )
    # 1 = inserted; 0 or 2 = the row was already there, so somebody else is its
    # creator and this call did not make it. In that case the name it was
    # created with wins, so it is read back rather than assumed.
    created = cursor.rowcount == 1
    if created:
        return cursor.lastrowid, name, True
    topic_id = cursor.lastrowid
    cursor.execute("SELECT name FROM topics WHERE id = %s", (topic_id,))
    row = cursor.fetchone()
    return topic_id, (row[0] if row else name), False


def confirmed_words():
    """Every word a dictionary has confirmed is real English (#258).

    Lower-cased, as a set, because the one caller feeds it straight into
    `games.pseudowords(known=...)` -- which is the whole point of keeping
    them. A word that was disputed once and settled is never offered as
    invented again, for anybody: whether a word is English is not a per-user
    fact.

    A dead database gives an empty set rather than raising. The cost is that a
    settled word could be offered again on a day the database is down, which is
    the same day nothing else works either -- and the alternative is a game
    that will not start.
    """
    try:
        conn = get_db_connection()
    except Exception:
        return set()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT word FROM confirmed_words")
        rows = cursor.fetchall()
        cursor.close()
        return {(row[0] or "").lower() for row in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def remember_confirmed_word(word, source):
    """Record that `word` is real English, once (#258).

    Returns True when this call is what added it -- the caller logs on that,
    so a second learner disputing the same word does not write a second line
    about a settled question.

    `INSERT ... ON DUPLICATE KEY UPDATE id = id` rather than a check and an
    insert, for the reason `_get_or_create_topic()` gives: two learners can
    dispute the same word at once, and the loser of that race should get the
    winner's answer rather than an integrity error.
    """
    word = (word or "").strip()
    if not word:
        return False
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO confirmed_words (word, source) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE id = id", (word, source))
        added = cursor.rowcount == 1
        conn.commit()
        cursor.close()
        return added
    finally:
        conn.close()


def resolve_topic(name, viewer_id=None, admin=False, topic_id=None):
    """The one topic this visitor means by `name` (#382), or None.

    Returns `{"id", "name", "is_public", "created_by_user_id"}`.

    A name is no longer a key. Two rows can hold one name -- the public topic
    and a private one -- so this resolves the same way `_get_or_create_topic()`
    files a card: **the visitor's own first, then the public one.** Nobody but
    the admin can see more than one candidate, which is why the ambiguity has
    exactly one reader.

    `topic_id` settles it outright, and is how the browse page links the admin
    to a private topic that shares a name with something else. It is still
    checked against the visibility rule: an id in a URL is a guess like any
    other.

    None means "no topic you may see by that name", which the topic page turns
    into a 404 -- a private topic must be refused when named, not merely left
    out of the lists that name it.
    """
    name = (name or "").strip()
    if not name and topic_id is None:
        return None
    visible, visible_params = _visible_clause(viewer_id, admin)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        columns = ("SELECT t.id, t.name, t.is_public, t.created_by_user_id, "
                   # The creator's name, for the one visitor who can see a
                   # topic that is not theirs: a padlock the admin cannot
                   # attribute is a padlock they have to go and query for.
                   "       COALESCE(u.preferred_name, u.display_name, u.email) "
                   "FROM topics t LEFT JOIN users u "
                   "  ON u.id = t.created_by_user_id ")
        if topic_id is not None:
            cursor.execute(
                columns + "WHERE t.id = %s" + visible,
                (topic_id,) + visible_params)
        else:
            cursor.execute(
                columns + "WHERE t.name = %s" + visible +
                # Yours first, then the public one -- and "yours" is the
                # **namespace**, not the creator: a learner who created the
                # public 'Work' as well as their own private one would
                # otherwise tie on the creator and be handed the public row,
                # which is not the topic they mean. The final tiebreak is the
                # id, so that the admin -- who can see several -- gets a stable
                # answer rather than whichever row the engine felt like.
                " ORDER BY (t.namespace = %s) DESC, t.is_public DESC,"
                " t.id LIMIT 1",
                (name,) + visible_params + (viewer_id,))
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "is_public": bool(row[2]),
            "created_by_user_id": row[3], "creator": row[4]}


def private_topics(viewer_id=None, admin=False):
    """The private topics this visitor can see, as `{name: {...}}` (#382).

    A map beside the page's data rather than a third element in
    `get_topics_by_section()`'s pairs -- the shape #223's icons already
    established, and for the same reason: that pair is read by the index page,
    the move dialog and the Mykola widget's own renderer, and widening it would
    be a change to all three for something only the browse page draws.

    Carries the id, because the admin is the one visitor who can see two topics
    with the same name and needs a link that says which. And the creator's
    name, because a padlock that does not say *whose* is no use to somebody
    monitoring the deck.
    """
    visible, visible_params = _visible_clause(viewer_id, admin)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT t.id, t.name, t.created_by_user_id, "
            "       COALESCE(u.preferred_name, u.display_name, u.email) "
            "FROM topics t LEFT JOIN users u ON u.id = t.created_by_user_id "
            "WHERE t.is_public = 0" + visible + " ORDER BY t.name, t.id",
            visible_params)
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return {name: {"id": topic_id, "created_by_user_id": creator,
                   "creator": owner, "mine": creator is not None
                   and creator == viewer_id}
            for topic_id, name, creator, owner in rows}


def set_topic_visibility(topic_id, public, viewer_id=None, admin=False):
    """Make one topic public or private (#382). Returns a status string.

    - `"changed"`    -- done;
    - `"unchanged"`  -- it was already that way;
    - `"denied"`     -- not this visitor's topic to change;
    - `"nobodys"`    -- the topic has no creator, so it cannot be private;
    - `"shared"`     -- it holds cards other people added;
    - `"taken"`      -- the name is already used in the namespace it would move
      into;
    - `"missing"`    -- no such topic.

    **`shared` is the rule that keeps the promise simple.** A topic may hold
    anyone's cards, so hiding one could take a card out of the deck of the
    learner who added it -- silently, and from a page they are not looking at.
    Refusing the flip means "private" needs no asterisk: everything in a
    private topic belongs to the one person who can see it. Cards with no owner
    at all count as somebody else's: the seeded deck (#203) is unowned, and it
    is shared by construction.

    Both columns are written in **one** statement, because `namespace` is the
    app's job only for want of a generated column MySQL would not give us (see
    schema.sql), and the two disagreeing is the one way this feature breaks
    quietly: the key would then be reserving the wrong name.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, is_public, created_by_user_id FROM topics "
            "WHERE id = %s", (topic_id,))
        row = cursor.fetchone()
        if row is None:
            return "missing"
        name, is_public, creator = row[0], bool(row[1]), row[2]
        if not admin and (creator is None or creator != viewer_id):
            return "denied"
        if public == is_public:
            return "unchanged"
        if not public:
            if creator is None:
                return "nobodys"
            cursor.execute(
                "SELECT 1 FROM flashcards WHERE topic_id = %s "
                "  AND (added_by_user_id IS NULL OR added_by_user_id <> %s) "
                "LIMIT 1",
                (topic_id, creator))
            if cursor.fetchone() is not None:
                return "shared"
        namespace = 0 if public else creator
        try:
            cursor.execute(
                "UPDATE topics SET is_public = %s, namespace = %s "
                "WHERE id = %s",
                (1 if public else 0, namespace, topic_id))
        except mysql.connector.IntegrityError:
            # uq_topics_namespace: a public topic of that name already exists,
            # or this learner already has a private one. The database answering
            # rather than a check beforehand is deliberate -- a check has a gap
            # between the question and the write, and this is the guarantee.
            return "taken"
        conn.commit()
        cursor.close()
        return "changed"
    finally:
        conn.close()


def place_topic(name, section, position, created_by_user_id=None):
    """Put the topic called `name` in `section` at `position` (#203).

    Returns `("created" | "moved" | "unchanged", topic_id)`.

    The counterpart to `_get_or_create_topic()`, and the only other way a topic
    row comes into existence. That one is reached from a *card* save and
    therefore knows nothing about curricula: it files a new topic under 'Other'
    at position 0, which is right for a topic somebody invented by looking a word
    up. This one is for a topic that is declared in advance — #203's eighteen,
    which belong in 'B2–C1 Conversational Topics', numbered from 1, before any
    of their cards exist. #215 built that section empty for exactly this.

    An existing topic of the same name is **moved**, not duplicated — the UNIQUE
    key means "the same name" is "the same topic", so a 'Work and careers'
    somebody had already started in 'Other' is that curriculum topic, and its
    cards come with it. Reported as `moved` rather than done quietly, because it
    is the one thing here that changes data somebody else created.

    A missing section raises: the section rows are a migration step, so their
    absence means `apply_schema.py` has not run, and a seed that silently filed
    eighteen topics under nothing would be worse than a stopped script.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("a topic needs a name")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM topic_sections WHERE name = %s", (section,))
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                f"no section called {section!r} — run apply_schema.py first")
        section_id = row[0]

        cursor.execute(
            "SELECT id, section_id, position FROM topics WHERE name = %s",
            (name,))
        existing = cursor.fetchone()
        if existing is not None:
            topic_id, current_section, current_position = existing
            if (current_section, current_position) == (section_id, position):
                cursor.close()
                return "unchanged", topic_id
            cursor.execute(
                "UPDATE topics SET section_id = %s, position = %s WHERE id = %s",
                (section_id, position, topic_id))
            conn.commit()
            cursor.close()
            applog.topic_placed(name, section, position, topic_id=topic_id)
            return "moved", topic_id

        # Same race as _get_or_create_topic: a card save can create this very
        # topic between the SELECT and here, and LAST_INSERT_ID(id) lets the
        # loser read the winner's id back instead of failing on the unique key.
        cursor.execute(
            "INSERT INTO topics (name, created_by_user_id, section_id, position) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "id = LAST_INSERT_ID(id), section_id = VALUES(section_id), "
            "position = VALUES(position)",
            (name, created_by_user_id, section_id, position))
        created = cursor.rowcount == 1
        topic_id = cursor.lastrowid
        conn.commit()
        cursor.close()
    finally:
        conn.close()

    if created:
        applog.topic_created(name, topic_id=topic_id,
                             created_by=created_by_user_id)
        return "created", topic_id
    # The race's loser: the row is somebody else's, but the section and position
    # were applied by the upsert, so the outcome is the same as a move.
    applog.topic_placed(name, section, position, topic_id=topic_id)
    return "moved", topic_id


def save_flashcard(entry, added_by_user_id=None, allow_duplicate=False):
    """
    Insert a flashcard entry into the `flashcards` table, unless a card with
    the same word and part of speech already exists anywhere in the database
    (issue #101) — repeated lookups used to pile up duplicate rows.

    Returns the new row id, or None when the card was skipped as a duplicate
    (callers use that to tell the user the word is already present).

    `allow_duplicate` writes the row anyway (#379). #101 exists to stop
    repeated lookups *piling up* rows nobody asked for, and that is still the
    default everywhere -- but a learner who has been shown the card they
    already have, and has answered "add it anyway", is not making that
    mistake. Only the review popup's confirmation passes it, and only for the
    card the popup marked: every other save path, Mykola's included (#308),
    keeps refusing.
    Ukrainian and Russian fields are optional; missing keys are stored as NULL.

    `added_by_user_id` (issue #89) records who saved the card; None means an
    anonymous visitor. It is a separate argument rather than a member of
    FLASHCARD_FIELDS on purpose: `entry` is built from submitted form data, so
    reading the id out of it would let a browser attribute a card to another
    user. Callers must take it from the server-side session.

    The same id also creates the *topic* when `entry["topic"]` names one that
    does not exist yet (#207) — one argument serving both, so the card's owner
    and the topic's creator cannot disagree about who was here.

    `topic` and `topic_id` are both written for the duration of the transition:
    the string is what ai_agent's cards_db still reads and the rollback if the
    id turns out wrong.
    """
    def serialize(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    columns = FLASHCARD_FIELDS + ("added_by_user_id", "topic_id")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Word matching follows the column's collation (case-insensitive by
        # default); the NULL-safe <=> lets pos-less cards (e.g. .mht imports)
        # be deduplicated too, which a UNIQUE index could not do — MySQL
        # treats NULLs in a unique key as distinct.
        if not allow_duplicate:
            cursor.execute(
                "SELECT id FROM flashcards WHERE word = %s AND pos <=> %s LIMIT 1",
                (entry.get("word"), entry.get("pos")),
            )
            if cursor.fetchone() is not None:
                cursor.close()
                return None
        # After the duplicate check, never before it: a card that is not going
        # to be written must not leave a new topic behind.
        topic_id, topic_name, topic_created = _get_or_create_topic(
            cursor, entry.get("topic"), added_by_user_id)
        values = tuple(
            # The topic column takes the canonical spelling, not the submitted
            # one, so it always matches the row topic_id points at.
            topic_name if field == "topic" else serialize(entry.get(field))
            for field in FLASHCARD_FIELDS
        )
        values += (added_by_user_id, topic_id)
        cursor.execute(
            f"INSERT INTO flashcards ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            values,
        )
        conn.commit()
        row_id = cursor.lastrowid
        cursor.close()
        if topic_created:
            # Logged here rather than by the caller, like set_user_blocked():
            # this is a write, CLAUDE.md's rule is that a write is logged, and
            # putting the line where the row appears is what makes an unlogged
            # topic impossible. No source field — the card's own CREATE line
            # follows immediately and carries it.
            applog.topic_created(topic_name, topic_id=topic_id,
                                 created_by=added_by_user_id)
        return row_id
    finally:
        conn.close()


def _is_empty(value):
    """Whether a stored column holds nothing worth keeping.

    `[]` counts, because the list fields are stored as JSON and an empty list
    is written as the two-character string rather than as NULL — so a card that
    has never had examples looks full to a plain `IS NULL` test.
    """
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict)):
        return not value
    text = str(value).strip()
    return text in ("", "[]", "{}", "null")


# What a later lookup may repair on a card that already exists. Deliberately
# not `word`, `pos` or `topic`: the first two identify the card, and moving it
# between topics has its own rules and its own ticket (#177).
# `explanation_source` is absent for a third reason: it is not a gap that can
# be repaired on its own. Filling it beside an explanation somebody else's
# lookup wrote would credit Wiktionary for Oxford's sentence, so it moves only
# with `explanation_en`, in the one place below that writes both (#390).
FILLABLE_FIELDS = tuple(
    f for f in FLASHCARD_FIELDS
    if f not in ("word", "pos", "topic", "explanation_source"))


def fill_missing_fields(entry):
    """Fill a card's **empty** columns from a fresh lookup. Returns what it
    filled, as a list of column names, or `[]` if there was nothing to do.

    The exit from the trap #349 would otherwise create. `save_flashcard()`
    refuses a duplicate `word` + `pos` (#101), so a card saved during a
    translator outage -- explanation and examples, no translations -- could
    never be improved: looking the word up again once the service returned
    would simply be skipped, and the card would stay half empty for good.

    **Only what is empty, and only from what the entry actually carries.** A
    stored value that holds anything wins, always: this repairs gaps and can
    never overwrite. That rule is what makes it safe to call on every skipped
    duplicate rather than only on ones somebody has inspected, and it is the
    same principle `update_flashcard()` follows for a key that is absent
    (#176) -- the difference being that this one also declines a key that is
    *present but empty*, since a failed lookup carries plenty of those.

    Not a permission check, and deliberately so: it adds nothing a card did not
    already claim about the same word, it cannot remove or alter anything, and
    the alternative -- leaving a card broken because somebody else made it --
    serves nobody. The caller still has to be allowed to save at all.
    """
    def serialize(value):
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    offered = {f: entry.get(f) for f in FILLABLE_FIELDS
               if f in entry and not _is_empty(entry.get(f))}
    if not offered:
        return []

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        # The same matching `save_flashcard()` deduplicates on, so this repairs
        # exactly the row that blocked the insert -- collation-driven on the
        # word, NULL-safe on the part of speech.
        cursor.execute(
            f"SELECT id, {', '.join(FILLABLE_FIELDS)} FROM flashcards "
            "WHERE word = %s AND pos <=> %s LIMIT 1",
            (entry.get("word"), entry.get("pos")),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return []

        filling = [f for f, value in offered.items() if _is_empty(row.get(f))]
        if not filling:
            cursor.close()
            return []

        # The credit rides along with the text it belongs to, and never alone
        # (#390). A card whose explanation is already there keeps whatever
        # source it has, including none.
        columns = list(filling)
        extra = ()
        if "explanation_en" in filling:
            columns.append("explanation_source")
            extra = (entry.get("explanation_source"),)

        cursor.execute(
            f"UPDATE flashcards SET {', '.join(f'{f} = %s' for f in columns)} "
            "WHERE id = %s",
            tuple(serialize(offered[f]) for f in filling) + extra
            + (row["id"],),
        )
        conn.commit()
        cursor.close()
        return filling
    finally:
        conn.close()


# What an edit may change (issue #176). `topic` is deliberately absent: moving
# a card between topics has different rules and its own ticket (#177), and
# `added_by_user_id` is never editable at all — it is the answer to "whose card
# is this?", which is the question the edit permission itself depends on.
# Not `explanation_source`: it is a fact about where the text came from, not a
# field anybody edits, and a submitted one would let a form claim an
# attribution (#390). `update_flashcard()` clears it instead, because an
# explanation somebody has rewritten is no longer the dictionary's sentence.
EDITABLE_FIELDS = tuple(
    f for f in FLASHCARD_FIELDS if f not in ("topic", "explanation_source"))


def duplicate_topic(word, pos):
    """The topic holding the card that blocks this word + part of speech (#308).

    `find_duplicate()` below answers *who owns* the blocking card, which is what
    #186 needs to say whether the learner can even see it. This answers *where
    it is*, which is what Mykola needs to stop sending somebody to a topic the
    card was never filed under: duplicate detection is global (#101) while
    topics are not, so "already saved" and "saved where you asked" are different
    facts.

    Kept beside `find_duplicate()` rather than widening its tuple: that shape is
    stubbed by the edit-path tests, and a third element only one caller reads
    would break them for a message nicety. The **match must stay identical** to
    `find_duplicate()`'s and `save_flashcard()`'s — collation-driven and
    NULL-safe on `pos` — or this would name the topic of a card that is not the
    one that did the blocking.

    None when there is no such card, which a caller reached through a skipped
    save should never see; it is the honest answer for a row deleted in the
    moment between the two queries.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic FROM flashcards WHERE word = %s AND pos <=> %s LIMIT 1",
            (word, pos),
        )
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else None
    finally:
        conn.close()


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


def _pos_match_key(pos):
    """How `save_flashcard()`'s duplicate check sees a part of speech.

    `pos <=> %s` under the column's collation: NULL-safe, so two pos-less cards
    are duplicates of each other, and case-insensitive. Not `parsers._pos_key()`
    -- that maps synonyms (`modal verb` and `auxiliary verb` are one part of
    speech to #228), which is a question about matching a *dictionary entry* to
    a card. The database has no such rule, and a chip that promised one would
    be describing a duplicate check that does not exist.
    """
    return (pos or "").strip().lower() or None


def find_saved_words(pairs):
    """What the deck already holds for each (word, pos) about to be offered (#377).

    One query for a whole review popup rather than one per card: a notes upload
    can carry thirty, and the answer is wanted while the page is being built,
    before anything is pressed.

    Returns a list aligned with `pairs`, each item:

    - `exact`   -- `(id, added_by_user_id)` of the card that already holds this
      word **and** part of speech, or None. This is precisely what #101 will
      refuse, so it is what decides whether pressing Add writes anything;
    - `others`  -- the other parts of speech this word is already saved under,
      in the spelling they are stored in. A card here still saves; the learner
      simply has the word already, which is what #145 warns about one step
      earlier in the lookup panel.

    Matching mirrors `save_flashcard()` exactly and deliberately -- the whole
    value of the chip is that it predicts what Add will do.
    """
    words = [word for word, _ in pairs if word]
    saved = {}
    if words:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            places = ", ".join(["%s"] * len(words))
            cursor.execute(
                "SELECT word, pos, id, added_by_user_id FROM flashcards "
                f"WHERE word IN ({places})",
                tuple(words),
            )
            for word, pos, card_id, owner in cursor.fetchall():
                saved.setdefault((word or "").strip().lower(), []).append(
                    (pos, card_id, owner))
            cursor.close()
        finally:
            conn.close()

    states = []
    for word, pos in pairs:
        stored = saved.get((word or "").strip().lower(), [])
        key = _pos_match_key(pos)
        exact = next(((card_id, owner) for stored_pos, card_id, owner in stored
                      if _pos_match_key(stored_pos) == key), None)
        others = []
        for stored_pos, _card_id, _owner in stored:
            if _pos_match_key(stored_pos) != key and stored_pos not in others:
                others.append(stored_pos)
        states.append({"exact": exact, "others": others})
    return states


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
        # The credit follows the text, in one rule (#390). A new explanation
        # carries whatever source the caller vouches for, and **absent means
        # none**: an explanation somebody rewrote is no longer the
        # dictionary's sentence, and a credit left on it would attribute their
        # words to the people who wrote the original. Not part of `changed`,
        # which records what somebody edited -- nobody edited this.
        #
        # One column serves two fields since the examples came too, and the
        # gap that leaves was looked at and **accepted**: a learner who
        # rewrites the explanation and keeps the dictionary's example
        # sentences drops the credit from those as well. The alternatives were
        # a second `examples_source` column, or keeping a credit that would
        # then name Wiktionary above the learner's own prose. If this needs
        # revisiting, the second column is the shape -- written beside the
        # examples, cleared when they change, exactly as this one is.
        if "explanation_en" in changed:
            assignments += ", explanation_source = %s"
            values += (entry.get("explanation_source"),)
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


def move_flashcard(card_id, to_topic, owner_id=None, admin=False):
    """Move one card to another topic (issue #177).

    Returns `(outcome, detail)`:

    - `("moved", (word, from_topic))` — `from_topic` so the caller can tell
      whether the topic it came from still exists
    - `("unchanged", None)` — it is already filed there
    - `("denied", None)` / `("missing", None)` — as elsewhere

    Separate from `update_flashcard()` because the rules genuinely differ, not
    because the SQL does. **No duplicate check applies**: `save_flashcard()`
    deduplicates on word+pos *globally*, never per topic, so moving a card
    between topics cannot create a duplicate — where renaming its word can.

    A move to an unknown topic still creates it (#207 keeps that promise, which
    the move dialog makes in so many words), and moving the last card out of a
    topic still removes it from `get_topics()` — the topic row survives, but the
    list is of topics that *have cards*, so the visible effect is unchanged.

    Ownership is the same conditional-UPDATE shape as everything else that
    changes a card: a move is treated as an edit, since a card sitting in a
    shared topic is still its author's.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT word, topic, topic_id, added_by_user_id "
            "FROM flashcards WHERE id = %s",
            (card_id,))
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return "missing", None
        word, from_topic, from_topic_id, card_owner = row
        if from_topic == to_topic:
            cursor.close()
            return "unchanged", None
        # Refuse before creating the destination topic, so a move somebody is
        # not allowed to make does not leave a new topic behind. This is *not*
        # the permission check — the conditional UPDATE below still is, and it
        # is what closes the gap between deciding and doing. Should ownership
        # change in between, the UPDATE refuses anyway and the only cost is an
        # empty topic row.
        # `owner_id is None` is refused rather than compared: an anonymous
        # caller owns nothing, and `added_by_user_id = NULL` never matches, so
        # the UPDATE could only ever deny it — comparing None to an unowned
        # card's None would agree and create a topic for a move that then fails.
        if not admin and (owner_id is None or card_owner != owner_id):
            cursor.close()
            return "denied", None

        # The mover is the creator, admin or not: app.py passes the acting
        # user's own id as owner_id either way.
        topic_id, topic_name, topic_created = _get_or_create_topic(
            cursor, to_topic, owner_id)
        # Identity, not spelling. The check above catches the ordinary no-op
        # cheaply; this one catches 'emotions' → 'EMOTIONS', which names the
        # same topic. It has to happen here because the destination is only
        # known to be that topic once the name has been resolved — and without
        # it the UPDATE would write the values the row already holds, report no
        # affected rows, and be read as a refusal.
        if topic_id == from_topic_id:
            cursor.close()
            return "unchanged", None

        if admin:
            cursor.execute(
                "UPDATE flashcards SET topic = %s, topic_id = %s WHERE id = %s",
                (topic_name, topic_id, card_id))
        else:
            cursor.execute(
                "UPDATE flashcards SET topic = %s, topic_id = %s "
                "WHERE id = %s AND added_by_user_id = %s",
                (topic_name, topic_id, card_id, owner_id))
        if cursor.rowcount == 0:
            cursor.close()
            return "denied", None
        conn.commit()
        cursor.close()
        if topic_created:
            applog.topic_created(topic_name, topic_id=topic_id,
                                 created_by=owner_id)
        return "moved", (word, from_topic)
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
        # Their private topics become public first (#382). `fk_topics_user` is
        # ON DELETE SET NULL, so a moment later this topic has no creator -- and
        # a private topic with no creator is one **nobody** can see and nobody
        # can un-hide, since every visibility test is `created_by_user_id = me`
        # and the control belongs to the creator. Its name would also stay
        # reserved in a namespace whose owner no longer exists.
        #
        # Public is the only outcome that leaves the deck usable, and it is the
        # honest one: a departing account's cards either stay as community
        # property or go, and this is the same question answered the same way
        # for the topic around them.
        cursor.execute(
            "SELECT id, name FROM topics "
            "WHERE created_by_user_id = %s AND is_public = 0", (user_id,))
        for topic_id, name in list(cursor.fetchall()):
            try:
                cursor.execute(
                    "UPDATE topics SET is_public = 1, namespace = 0 "
                    "WHERE id = %s", (topic_id,))
                continue
            except mysql.connector.IntegrityError:
                pass
            # A public topic already holds that name, so this one cannot take
            # it. Its cards move there and the empty private row goes: that is
            # where they would have been if the topic had never been private,
            # and it is the only outcome that neither loses a card nor fails
            # the deletion the learner asked for. An empty topic row is kept
            # everywhere else (#207) because its name, creator and age are
            # worth keeping -- none of which survives here anyway.
            cursor.execute(
                "SELECT id FROM topics WHERE name = %s AND namespace = 0",
                (name,))
            public = cursor.fetchone()
            if public is None:            # cannot happen; do not lose the row
                continue
            cursor.execute(
                "UPDATE flashcards SET topic_id = %s, topic = %s "
                "WHERE topic_id = %s", (public[0], name, topic_id))
            cursor.execute("DELETE FROM topics WHERE id = %s", (topic_id,))
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

    Qualified with `f.` — both callers join `topics` now (#207), and this is
    about who owns the *card*. A topic's own `created_by_user_id` is attribution
    and must never end up filtering a view.
    """
    if owner_id is None:
        return "", ()
    return " AND f.added_by_user_id = %s", (owner_id,)


def _visible_clause(viewer_id, admin=False):
    """SQL and parameters hiding topics this visitor may not see (#382).

    **Not `_owner_clause()`, and not a variant of it.** That one is #127's
    setting -- a preference about whose *cards* a learner wants to look at,
    which they can switch off. This is a permission about a *topic*, and no
    setting reaches past it: `owner_id` is None whenever the preference is off,
    where `viewer_id` is simply who is asking, always.

    Qualified with `t.`, and reading `t.created_by_user_id` -- the column the
    table's own comment described as attribution that nothing reads to decide
    permissions. #382 is what made that sentence out of date.

    An anonymous visitor (`viewer_id` None) sees public topics only: `= NULL`
    is never true, so writing the comparison anyway would work, but saying it
    plainly is worth more than the shared branch. The admin (#158) sees
    everything, which is the one deliberate hole in the promise and is why the
    user guide says "only you and the admin" rather than "only you".
    """
    if admin:
        return "", ()
    if viewer_id is None:
        return " AND t.is_public = 1", ()
    return " AND (t.is_public = 1 OR t.created_by_user_id = %s)", (viewer_id,)


def get_topics(owner_id=None, viewer_id=None, admin=False):
    """
    Return all topics that have flashcards, as (topic, card_count) tuples
    sorted by topic name.

    With `owner_id`, only that user's cards are counted (#127), so a topic
    made entirely of other people's cards disappears from the list rather
    than opening empty.

    Still a list of topics that **have cards**, now by joining `topics` rather
    than grouping strings (#207). That is a deliberate choice, and the reason
    this change is invisible on the page: the old GROUP BY could not report an
    empty topic, so neither does this.

    Empty topic rows do occur — deleting the last card in a topic leaves one,
    and so does deleting an account that chose to take its cards with it. They
    are kept rather than tidied away, because the row is now where the topic's
    name, creator and age live: file a card under that name again and it is the
    same topic resurrected, not a new one wearing the name. What should happen
    on a page for a topic that is currently empty — list it, hide it, show it
    only to its creator — is a question for whichever ticket first *wants* one:
    a seeded curriculum (#203), or an image hung on a topic before it is filled
    (#185). Answering it here, with no way to see the answer, would be guessing.

    The name comes from the `topics` row, so it is one spelling from one place,
    where the old GROUP BY reported whichever case-variant the engine happened
    to pick.

    Returns `(name, count)` tuples, as it always has: the topic pages, the tiles
    on the index and the move dialog's suggestions all key on the name, and
    URLs stay readable because of it.
    """
    clause, params = _owner_clause(owner_id)
    visible, visible_params = _visible_clause(viewer_id, admin)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT t.name, COUNT(*) FROM flashcards f "
            "JOIN topics t ON f.topic_id = t.id "
            "WHERE f.topic_id IS NOT NULL" + clause + visible +
            " GROUP BY t.id, t.name ORDER BY t.name",
            params + visible_params,
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return rows


def get_topics_by_section(owner_id=None, alphabetical=False, viewer_id=None,
                          admin=False):
    """The browse page's topics, grouped under their section (#218).

    Returns `[(section_name, [(topic_name, count), ...]), ...]` ordered by
    `(section.position, topic.position, topic.name)` — #215's rule. The inner
    pairs are exactly what `get_topics()` returns, so a template can render
    either shape with the same tile.

    `alphabetical=True` (#363) drops `topic.position` from that key, leaving
    the name. Not a different sort — the *same* one the rule already ends on,
    asked for on its own, which is why `Other` looks identical either way:
    every topic there holds position 0 and is already ordered by name. Kept in
    SQL for that reason as well. Doing it in Python would re-decide the
    collation, the case-insensitivity and the Cyrillic ordering that this page
    has always had, on the day a setting was added about something else.

    Sections keep their own order regardless: this orders topics within one.

    `get_topics()` is deliberately left alone rather than grown a grouping
    argument: it still answers "which topics are there", which is what
    /topics.json's callers, the move dialog's suggestions and #178 will keep
    asking. Grouping is a different question and gets its own function.

    **Every section is returned, including one with nothing to show.** The
    B2–C1 shelf is empty until #203 fills it, and a heading that appeared only
    once it had content could not do the one job it has — saying what the deck
    is going to be. A caller that wants "nothing here at all" to read
    differently checks for it; the index page shows its existing hint instead.

    A *topic* with no cards is still omitted, exactly as `get_topics()` omits
    it (#207): an empty topic row is kept for its name and creator, not offered
    as somewhere to browse. That is the asymmetry — an empty section is
    structure, an empty topic is a leftover.

    The owner filter (#127) rides in the LEFT JOIN's ON clause, not a WHERE.
    In a WHERE it would discard the very rows the outer join exists to keep,
    turning every section with no cards *of yours* into no section at all.

    Topics join their section with an inner join: #215 documented section_id as
    never NULL in a settled database. A topic that somehow had none is missing
    from this page while still reachable by URL and still in `get_topics()`,
    and the next `apply_schema.py` adopts it.
    """
    clause, params = _owner_clause(owner_id)
    visible, visible_params = _visible_clause(viewer_id, admin)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # The sections, first and on their own. One query cannot do both jobs:
        # the row that would carry an empty section is a (section, no topic)
        # row, and it is indistinguishable from the (section, topic with no
        # cards) rows that have to be discarded. Asked separately, "which
        # sections are there" has an unambiguous answer.
        cursor.execute(
            "SELECT id, name FROM topic_sections ORDER BY position, name")
        sections = cursor.fetchall()

        # Then the topics worth showing, with their counts. The owner filter
        # sits in the ON clause so that a topic holding only other people's
        # cards comes back with a count of 0 and is dropped by the HAVING,
        # rather than vanishing before it can be counted.
        # The visibility clause is a WHERE, where the owner filter above is an
        # ON (#382): the owner filter is about the *cards* and has to leave a
        # topic standing with a count of 0, but a topic this visitor may not
        # see should not be counted, grouped or returned at all.
        cursor.execute(
            "SELECT t.section_id, t.name, COUNT(f.id) "
            "FROM topics t "
            "LEFT JOIN flashcards f ON f.topic_id = t.id" + clause +
            (" WHERE 1=1" + visible if visible else "") +
            " GROUP BY t.id, t.section_id, t.name, t.position "
            "HAVING COUNT(f.id) > 0 "
            + ("ORDER BY t.name" if alphabetical
               else "ORDER BY t.position, t.name"),
            params + visible_params,
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    by_section = {}
    for section_id, name, count in rows:
        by_section.setdefault(section_id, []).append((name, int(count)))
    # A topic whose section_id matches nothing — the NULL case above — is left
    # out here rather than tested for.
    return [(name, by_section.get(section_id, []))
            for section_id, name in sections]


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


def get_flashcards_by_topic(topic, owner_id=None, viewer_id=None, admin=False):
    """
    Fetch all flashcards for the given topic as a list of dictionaries.
    The examples_* fields are deserialized back into lists.

    With `owner_id`, only that user's cards come back (#127). None means
    every card, as it always has — see _owner_clause().

    Still keyed by topic **name** (#207): it arrives from the URL, and a name
    that matches no topic returns no cards, which is what makes
    /flashcards/<anything> render an empty page rather than a 404 — behaviour
    the topic pages have always had and #178 will have to think about.

    One topic is the common case and keeps its own name, but not its own query
    (#248): it is `get_flashcards_by_topics()` with a list of one. Ordering is
    unchanged — that function's `t.name, f.word` is `f.word` when there is only
    one topic to order by.
    """
    return get_flashcards_by_topics([topic], owner_id, viewer_id, admin)


def get_flashcards_by_topics(topics, owner_id=None, viewer_id=None,
                             admin=False):
    """Fetch the flashcards of **several** topics in one query (#248).

    Every activity in #233 draws from a selection of topics rather than one, so
    the alternative is `get_flashcards_by_topic()` in a loop — a connection and
    a round trip per ticked topic, eighteen of them for a learner who selects
    the whole B2–C1 curriculum.

    `topics` is a sequence of names; duplicates are harmless, since `IN` matches
    a row once however many times its topic was asked for. The owner filter
    (#127) applies exactly as it does to one topic: this is about who owns the
    *card*, not who created the topic.

    **An empty sequence returns no cards, and never every card.** That is the
    same trap `_owner_clause()` documents for `owner_id` — a value meaning "no
    filter" and a value meaning "nothing" look alike, and guessing wrong here
    would quietly play a round over the entire deck. #233's rule that a missing
    `?topic=` means the whole visible deck is a *routing* decision, resolved
    into an explicit list of names before this is called; see
    `games.resolve_selection()`.

    **`topic` comes from the topics row, not from `flashcards.topic`** (#269).
    The two hold the same text today -- #207's backfill made the column
    canonical -- but the column is what #210 exists to drop, and a caller that
    groups by it would break silently when that lands. Selected after `f.*` so
    it shadows the column rather than sitting beside it under another name,
    which keeps every existing caller reading the same key.

    Ordered by `(topic name, word)` so a result is deterministic and a caller
    that wants to group by topic can. Games shuffle what they get, so the order
    is for tests and for the quiz rather than for play.
    """
    names = [name for name in (topics or []) if name]
    if not names:
        return []

    clause, params = _owner_clause(owner_id)
    visible, visible_params = _visible_clause(viewer_id, admin)
    placeholders = ", ".join(["%s"] * len(names))
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        # The visibility clause matters most *here* (#382). A name reaches this
        # from a URL or a remembered selection, so a topic left out of every
        # list is still asked for by anyone who kept the link -- and two topics
        # can now share a name, one public and one private, which is the other
        # reason a name alone is not enough to decide what comes back.
        cursor.execute(
            "SELECT f.*, t.name AS topic "
            "FROM flashcards f JOIN topics t ON f.topic_id = t.id "
            f"WHERE t.name IN ({placeholders})" + clause + visible +
            " ORDER BY t.name, f.word",
            tuple(names) + params + visible_params,
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    for row in rows:
        for field in LIST_FIELDS:
            row[field] = _to_list(row.get(field))
    return rows
