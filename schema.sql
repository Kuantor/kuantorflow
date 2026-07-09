-- KuantorFlow database schema
-- Apply with: mysql -u <user> -p -h <host> <database> < schema.sql

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
