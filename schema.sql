-- KuantorFlow database schema
-- Apply with: python apply_schema.py
--
-- This file defines a *fresh* database and holds CREATE TABLE statements only.
-- Re-applying it cannot change a table that already exists, so a change to one
-- — a new column, index or constraint — goes in MIGRATIONS in apply_schema.py
-- instead (issue #180). Piping this file into mysql by hand skips those.

-- Anonymous Mykola usage per day (issue #164). One row per day, incremented
-- atomically, so the daily ceiling holds across PythonAnywhere's worker
-- processes — an in-memory counter would reset on reload and a file would race.
CREATE TABLE IF NOT EXISTS anonymous_usage (
    day        DATE PRIMARY KEY,
    messages   INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Generated texts per day (issue #237). Same shape and same reason as
-- anonymous_usage above: a ceiling on something that costs money has to hold
-- across PythonAnywhere's worker processes, so it is counted in a row rather
-- than in memory. Two ceilings are counted here — one row per account per day,
-- and one row per day counting everybody.
--
-- user_id 0 is that everybody row, and it is 0 rather than the NULL #237's
-- comment describes because MySQL treats NULLs in a unique key as distinct: an
-- ON DUPLICATE KEY UPDATE against a NULL user_id inserts a second row every
-- time instead of incrementing the first, and a PRIMARY KEY cannot hold NULL at
-- all. users.id is AUTO_INCREMENT and starts at 1, so 0 is free and can mean
-- only this.
--
-- **No foreign key to users**, unlike every other user_id in this file. These
-- are day-scoped counters, not attribution: a deleted account leaving one stale
-- row that expires the same day is better than another RESTRICT/CASCADE
-- decision on the account-deletion path (#165).
CREATE TABLE IF NOT EXISTS text_generation_usage (
    day        DATE NOT NULL,
    user_id    INT NOT NULL DEFAULT 0,
    texts      INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (day, user_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Word lookups per day (#388). The lookup panel is the one paid path anybody
-- could reach: `parse_word` asks the translator once per language and then the
-- dictionary, and since #353 the translator is a licensed API on our own key.
-- Uncapped that is a metered spend with no ceiling, which is the failure #200
-- fixed for uploads and #237 never had.
--
-- Shaped like text_generation_usage above, with one difference that is the
-- whole design: **the row whose user_id is 0 counts anonymous lookups only**,
-- not everybody. A signed-in learner meets their own daily ceiling; a visitor
-- meets the shared one. So an anonymous run cannot exhaust what the people who
-- signed up are allowed to spend -- the failure #199 names for #164's shared
-- ceiling, where one person in a loop spends the day's budget for everyone.
--
-- Zero rather than the NULL that would read more naturally, for the reason
-- #237 found: MySQL treats NULLs in a unique key as distinct, so an upsert
-- against a NULL user_id inserts a fresh row every time instead of
-- incrementing the first, and a PRIMARY KEY cannot hold NULL at all.
--
-- No foreign key to `users`, also as in #237: these are day-scoped counters
-- rather than attribution, and a stale row that expires the same day beats
-- another RESTRICT/CASCADE decision on the account-deletion path (#165).
CREATE TABLE IF NOT EXISTS word_lookup_usage (
    day        DATE NOT NULL,
    user_id    INT NOT NULL DEFAULT 0,
    lookups    INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (day, user_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Words a learner disputed and a dictionary confirmed (#258).
--
-- *Real or fake* invents words with a character trigram trained on the deck,
-- and a model trained on English sometimes produces English: the learner is
-- then marked wrong for being right, which is the one bug in a teaching app
-- that costs trust rather than time. #132's filters ask "does the deck know
-- this word", and the deck knows ~2,500 words where English has hundreds of
-- thousands, so no amount of cleverness with them closes the gap.
--
-- This is the other end of it: the learner challenges a word, a real lexicon
-- settles it, and the answer is kept so the same word is never offered as
-- invented again. The set grows from actual disagreements rather than from a
-- word list somebody has to ship and license.
--
-- **Not per user.** Whether a word is English is not a matter of opinion, so a
-- confirmation belongs to the deck rather than to whoever happened to press
-- the button. No foreign key to `users` for that reason -- and because
-- deleting an account must not un-confirm English (#165).
--
-- Append-only in practice: nothing removes a row, and `word` is unique so a
-- second confirmation of the same word is a no-op rather than a duplicate.
CREATE TABLE IF NOT EXISTS confirmed_words (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    word         VARCHAR(255) NOT NULL,
    -- Which lexicon said so, so a later reader can tell a Wiktionary
    -- confirmation from an Oxford one -- they answer different halves of
    -- English, and knowing which one settled a dispute is worth a column.
    source       VARCHAR(32) NOT NULL,
    confirmed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_confirmed_words (word)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Signed-in identities (issue #148). Keyed on google_sub, Google's OIDC
-- subject: it is unique per account and never changes, where an email can be
-- changed by its owner. Email is ordinary updatable data.
-- given_name/family_name come from the Google claims verbatim rather than
-- being split out of display_name, which guesses wrong on several given names
-- and on family-name-first locales. preferred_name is what Mykola calls the
-- user; NULL means "use the real first name".
CREATE TABLE IF NOT EXISTS users (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    google_sub     VARCHAR(255) NOT NULL UNIQUE,
    email          VARCHAR(255) NOT NULL UNIQUE,
    display_name   VARCHAR(255),
    given_name     VARCHAR(255),
    family_name    VARCHAR(255),
    preferred_name VARCHAR(255),
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at   TIMESTAMP NULL DEFAULT NULL,
    -- Blocked accounts (issue #126). A nullable timestamp rather than a
    -- boolean: it answers "is this account blocked?" and "since when?" from
    -- one column. NULL means not blocked, so an account is unblocked by
    -- clearing it and nothing else. The reason is admin-facing only — the
    -- blocked user is shown the admin's address, not this text, because it is
    -- a note to whoever unblocks them later.
    blocked_at     TIMESTAMP NULL DEFAULT NULL,
    blocked_reason VARCHAR(255),
    INDEX idx_email (email)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Topic sections (issue #215). A section groups topics into something a
-- learner reads as a curriculum instead of one flat alphabetical list. Two
-- exist to begin with: 'B2–C1 Conversational Topics', which #203's seeded deck
-- will fill, and 'Other', the bucket every topic that predates this table was
-- moved into.
--
-- The section *rows* are not here, because this file holds CREATE TABLE only
-- (#180) and apply_schema.py enforces it. They are inserted by a data step in
-- MIGRATIONS, which runs on a fresh database and an existing one alike — so a
-- new database still ends up with both sections.
--
-- Above `topics` for the usual reason: apply_schema.py runs these in file
-- order and topics' foreign key needs its target first.
--
-- `position` orders the sections themselves. The values are spaced rather than
-- consecutive ('Other' is 100) so a section can be slotted in front of it
-- later without renumbering the ones that already exist.
CREATE TABLE IF NOT EXISTS topic_sections (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(255) NOT NULL,
    position   INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- UNIQUE for the same reason topics.name is: under utf8mb4_unicode_ci
    -- 'other' and 'Other' are one section, and the key stops the second
    -- spelling being stored as though it were a different one.
    UNIQUE KEY uq_topic_sections_name (name)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Topics (issue #207). Until now a topic was a VARCHAR on flashcards and the
-- topic *list* was a GROUP BY: correct while a topic was a bare label, and a
-- dead end as soon as one needs to own anything (an image, #185) or to be
-- renamed as a thing rather than as a string (#178).
--
-- Defined ABOVE flashcards on purpose: apply_schema.py runs these statements in
-- file order and flashcards' foreign key needs its target to exist first — the
-- same reason `users` sits above it.
--
-- name is UNIQUE, which finally makes "the same topic" a fact instead of a
-- coincidence of spelling. The column collation is utf8mb4_unicode_ci, so
-- 'Work' and 'work' were already one topic everywhere it mattered; the
-- constraint just stops the second spelling being stored as if it were another.
CREATE TABLE IF NOT EXISTS topics (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    -- Which section the topic sits in (#215). Nullable only because a column
    -- added to an existing table has to be, and because a topic saved during a
    -- deploy — after the column, before the backfill — has nowhere to point
    -- yet. In a settled database it is never NULL: the backfill adopts every
    -- topic that predates it and _get_or_create_topic() files new ones under
    -- 'Other', so nothing reading this needs a "no section" branch.
    section_id         INT NULL,
    -- The topic's index within its section (#215). Ordering is
    -- (section.position, topic.position, topic.name): the name is the final
    -- tiebreak, which is what makes 0 a usable default. Every topic in 'Other'
    -- holds 0 and therefore sorts alphabetically — the order get_topics() has
    -- always produced — and 0 means "no order decided here", honestly, rather
    -- than pretending a bucket is a curriculum. A section that *is* ordered
    -- (#203's sixteen) numbers its topics from 1.
    position           INT NOT NULL DEFAULT 0,
    -- Who created it, on the same terms as flashcards.added_by_user_id (#89):
    -- NULL means nobody's — an anonymous visitor, a seeding script, or a topic
    -- that predates the column. Attribution only; nothing reads it to decide
    -- permissions, and #127 keeps filtering on *card* ownership.
    created_by_user_id INT NULL,
    -- Who may see it (#382). True for every topic that predates the column and
    -- for every topic created since: a topic is public unless its creator says
    -- otherwise, and only its creator (and the admin, who monitors the deck)
    -- sees it when they do.
    --
    -- This is not #127's 'Use only individual cards', and the two must not be
    -- confused. That is a *preference*, per visitor, about which cards they
    -- want to look at; turn it off and the whole deck is back. This is a
    -- *permission*, and no setting reaches past it.
    is_public          TINYINT(1) NOT NULL DEFAULT 1,
    -- Which namespace the name is unique in (#382), and the whole of how
    -- private topics escape the shared one. Public rows all land in 0, so a
    -- public name is unique across the site exactly as it always was -- the
    -- shared deck stays shared, and every URL, link and remembered game
    -- selection that carries a *name* keeps working. A private row lands in
    -- its creator's id, so each learner may hold one private 'Work' and it can
    -- sit beside the public one.
    --
    -- Written by the app, not derived by the database, and **not** for want of
    -- trying. `IF(is_public, 0, created_by_user_id)` as a STORED generated
    -- column is what this wants to be, and MySQL 8.0.46 refuses it:
    --
    --     3823: Column 'created_by_user_id' cannot be used in a check
    --     constraint: needed in a foreign key constraint referential action
    --
    -- for a CHECK, and 1215 for the generated column. The cause is
    -- fk_topics_user's ON DELETE SET NULL just below, which #165 depends on and
    -- which is not worth trading away for a derivation. So the *uniqueness* is
    -- still the database's (the key below cannot be talked out of it, race or
    -- no race) and only the *derivation* is the app's -- in one UPDATE, in
    -- `set_topic_visibility()`, which is the only writer of either column.
    --
    -- A topic with **no creator** would land in (name, NULL) if it were ever
    -- private, and MySQL treats NULLs in a unique key as distinct -- so the key
    -- could not stop two private 'Orphan' rows. Nothing does:
    -- `set_topic_visibility()` refuses to make a creatorless topic private, and
    -- deleting an account makes that account's private topics public before the
    -- FK sets their creator to NULL (#165) -- otherwise they would be topics
    -- nobody can see and nobody can un-hide.
    namespace          INT NOT NULL DEFAULT 0,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Replaced uq_topics_name (name) in #382. On the pair, so that 'unique
    -- among public topics' and 'unique among one learner's private topics' are
    -- one key rather than two rules the app has to remember.
    UNIQUE KEY uq_topics_namespace (name, namespace),
    INDEX idx_topics_created_by (created_by_user_id),
    INDEX idx_topics_section (section_id),
    -- ON DELETE SET NULL, where flashcards uses RESTRICT — deliberately. #165
    -- chose RESTRICT because deleting an account asks a real question ("keep my
    -- cards or delete them?") that a cascade would answer silently. A topic has
    -- no such question: it may hold other people's cards, so deleting the
    -- creator's account cannot delete it, and surviving with no creator is the
    -- only correct outcome. RESTRICT here would make account deletion fail for
    -- anyone who ever created a topic — _delete_account_data() ends in
    -- delete_user(), and the user would be told "nothing was removed" with no
    -- way to fix it.
    CONSTRAINT fk_topics_user FOREIGN KEY (created_by_user_id)
        REFERENCES users (id) ON DELETE SET NULL,
    -- ON DELETE RESTRICT, unlike fk_topics_user just above (#215). The two
    -- foreign keys on this table answer different questions. A creator is
    -- attribution, so losing one leaves a topic that is simply nobody's. A
    -- section is where the topic *lives*, and deleting one asks "move these
    -- topics or delete them?" — the same question flashcards.topic_id has
    -- about a topic, settled the same way in #165. RESTRICT makes deleting a
    -- non-empty section fail loudly instead of quietly emptying it into NULL,
    -- which is the state the section_id comment above promises cannot happen.
    CONSTRAINT fk_topics_section FOREIGN KEY (section_id)
        REFERENCES topic_sections (id) ON DELETE RESTRICT
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS flashcards (
    id INT AUTO_INCREMENT PRIMARY KEY,
    word VARCHAR(255) NOT NULL,
    pos VARCHAR(20),
    explanation_en TEXT,
    -- Which dictionary wrote explanation_en (#390). NULL for every card saved
    -- before this column existed, for one built by a notes import or by
    -- Mykola, and for one whose explanation a learner has since edited -- all
    -- of which mean the same thing: nobody can say whose sentence this is.
    --
    -- It exists because Wiktionary's text is CC BY-SA and a credit is the
    -- condition of using it, and a card that cannot name its source cannot be
    -- credited. That is also why it could not wait: once two sources are mixed
    -- in one column, the rows already there are indistinguishable forever.
    explanation_source VARCHAR(40),
    examples_en TEXT,
    -- The same for the example sentences, and a separate column because the
    -- two are edited separately (#390). A learner who rewrites the definition
    -- and keeps the dictionary's sentences has changed one of them and not the
    -- other, and one column could only be wrong in one direction or the other:
    -- dropping a credit the examples still need, or keeping one over prose the
    -- learner wrote.
    examples_source VARCHAR(40),
    translation_ukr VARCHAR(255),
    examples_ukr TEXT,
    translation_rus VARCHAR(255),
    examples_rus TEXT,
    -- The topic name, kept alongside topic_id for the transition (#207). Both
    -- are written on every save. This column is what ai_agent's cards_db reads
    -- today, and it is the rollback if topic_id turns out wrong; phase 3 drops
    -- it once ai_agent joins `topics` instead. Do not read it here.
    topic VARCHAR(255),
    topic_id INT NULL,
    -- Who added the card (issue #89). NULL for anonymous visitors and for
    -- every card saved before this column existed — NULL is what SQL already
    -- means by "references nothing", and it keeps the foreign key valid where
    -- a sentinel like -1 would need a fake user row in every database.
    -- Beware that NULL also breaks intuitive comparisons: "everyone else's
    -- cards" (#127) needs IS NOT NULL / <=>, not != .
    added_by_user_id INT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_topic (topic),
    INDEX idx_topic_id (topic_id),
    INDEX idx_word (word),
    INDEX idx_added_by (added_by_user_id),
    -- ON DELETE RESTRICT on purpose (settled in #165): account deletion asks
    -- the user whether to keep or delete their cards, so the application must
    -- resolve them first. CASCADE would let a stray row deletion destroy
    -- someone's vocabulary, and SET NULL would silently pick "keep" even when
    -- they chose "delete". RESTRICT turns a forgotten step into a loud failure.
    CONSTRAINT fk_flashcards_user FOREIGN KEY (added_by_user_id)
        REFERENCES users (id) ON DELETE RESTRICT,
    -- RESTRICT again, and here for #165's original reason: a topic holding
    -- cards cannot simply vanish, because "move them or delete them?" is a
    -- question for the application to put to someone.
    CONSTRAINT fk_flashcards_topic FOREIGN KEY (topic_id)
        REFERENCES topics (id) ON DELETE RESTRICT
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- The ALTER statements that used to be listed here as comments — pos,
-- added_by_user_id, idx_added_by and fk_flashcards_user — are now real
-- statements in apply_schema.py, which runs them on databases that predate
-- them and skips them everywhere else.
