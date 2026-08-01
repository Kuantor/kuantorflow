-- KuantorFlow database schema
-- Apply with: mysql -u <user> -p -h <host> <database> < schema.sql

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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_topic (topic),
    INDEX idx_word (word)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- If the flashcards table already exists without the pos column, run:
-- ALTER TABLE flashcards ADD COLUMN pos VARCHAR(20) AFTER word;
