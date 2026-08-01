-- KuantorFlow database schema
-- Apply with: mysql -u <user> -p -h <host> <database> < schema.sql

-- Anonymous Mykola usage per day (issue #164). One row per day, incremented
-- atomically, so the daily ceiling holds across PythonAnywhere's worker
-- processes — an in-memory counter would reset on reload and a file would race.
CREATE TABLE IF NOT EXISTS anonymous_usage (
    day        DATE PRIMARY KEY,
    messages   INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP
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
    INDEX idx_email (email)
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
    topic VARCHAR(255),
    -- Who added the card (issue #89). NULL for anonymous visitors and for
    -- every card saved before this column existed — NULL is what SQL already
    -- means by "references nothing", and it keeps the foreign key valid where
    -- a sentinel like -1 would need a fake user row in every database.
    -- Beware that NULL also breaks intuitive comparisons: "everyone else's
    -- cards" (#127) needs IS NOT NULL / <=>, not != .
    added_by_user_id INT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_topic (topic),
    INDEX idx_word (word),
    INDEX idx_added_by (added_by_user_id),
    -- ON DELETE RESTRICT on purpose (settled in #165): account deletion asks
    -- the user whether to keep or delete their cards, so the application must
    -- resolve them first. CASCADE would let a stray row deletion destroy
    -- someone's vocabulary, and SET NULL would silently pick "keep" even when
    -- they chose "delete". RESTRICT turns a forgotten step into a loud failure.
    CONSTRAINT fk_flashcards_user FOREIGN KEY (added_by_user_id)
        REFERENCES users (id) ON DELETE RESTRICT
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- If the flashcards table already exists without the pos column, run:
-- ALTER TABLE flashcards ADD COLUMN pos VARCHAR(20) AFTER word;

-- If the flashcards table already exists without the added_by_user_id column
-- (issue #89), run — the users table (#148) has to be in place first:
-- ALTER TABLE flashcards ADD COLUMN added_by_user_id INT NULL AFTER topic;
-- ALTER TABLE flashcards ADD INDEX idx_added_by (added_by_user_id);
-- ALTER TABLE flashcards ADD CONSTRAINT fk_flashcards_user
--     FOREIGN KEY (added_by_user_id) REFERENCES users (id) ON DELETE RESTRICT;
