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
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_topics_name (name),
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
    examples_en TEXT,
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
