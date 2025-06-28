-- patch_schema.sql - Скрипт для оновлення існуючої схеми бази даних, якщо schema.sql не був виконаний повністю.

-- Додавання/оновлення таблиці custom_feeds, якщо її немає або потрібно оновити
CREATE TABLE IF NOT EXISTS custom_feeds (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    feed_name TEXT NOT NULL,
    filters JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, feed_name)
);

-- Додавання відсутніх стовпців до таблиці users
-- (Якщо ці стовпці вже існують, ALTER TABLE ADD COLUMN IF NOT EXISTS просто пропустить їх)
ALTER TABLE users ADD COLUMN IF NOT EXISTS safe_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS current_feed_id INT REFERENCES custom_feeds(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_expires_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS level INT DEFAULT 1;
ALTER TABLE users ADD COLUMN IF NOT EXISTS badges TEXT[] DEFAULT ARRAY[]::TEXT[];
ALTER TABLE users ADD COLUMN IF NOT EXISTS inviter_id INT REFERENCES users(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_notifications BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS view_mode TEXT DEFAULT 'manual';

-- Додавання/оновлення таблиці news, якщо її немає
CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    title TEXT,
    content TEXT,
    lang TEXT,
    country TEXT,
    tags TEXT[],
    ai_classified_topics TEXT[] DEFAULT ARRAY[]::TEXT[],
    source TEXT,
    link TEXT,
    published_at TIMESTAMP,
    expires_at TIMESTAMP,
    file_id TEXT,
    media_type TEXT,
    source_type TEXT,
    tone TEXT,
    sentiment_score REAL,
    citation_score INT DEFAULT 0,
    is_duplicate BOOLEAN DEFAULT FALSE,
    is_fake BOOLEAN DEFAULT FALSE,
    moderation_status TEXT DEFAULT 'pending'
);

-- Додавання відсутніх стовпців до таблиці news (якщо вона вже існувала, але без цих полів)
ALTER TABLE news ADD COLUMN IF NOT EXISTS ai_classified_topics TEXT[] DEFAULT ARRAY[]::TEXT[];
ALTER TABLE news ADD COLUMN IF NOT EXISTS link TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;
ALTER TABLE news ADD COLUMN IF NOT EXISTS file_id TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS media_type TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS tone TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS sentiment_score REAL;
ALTER TABLE news ADD COLUMN IF NOT EXISTS citation_score INT DEFAULT 0;
ALTER TABLE news ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN DEFAULT FALSE;
ALTER TABLE news ADD COLUMN IF NOT EXISTS is_fake BOOLEAN DEFAULT FALSE;
ALTER TABLE news ADD COLUMN IF NOT EXISTS moderation_status TEXT DEFAULT 'pending';

-- Додавання/оновлення таблиці sources
CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    link TEXT UNIQUE NOT NULL,
    type TEXT,
    added_by_user_id INT REFERENCES users(id),
    verified BOOLEAN DEFAULT FALSE,
    reliability_score INT DEFAULT 0,
    status TEXT DEFAULT 'active',
    last_parsed_at TIMESTAMP
);

-- Додавання відсутніх стовпців до таблиці sources
ALTER TABLE sources ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS reliability_score INT DEFAULT 0;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_parsed_at TIMESTAMP;


-- Додавання/оновлення таблиці interactions
CREATE TABLE IF NOT EXISTS interactions (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці reports
CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці feedback
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці user_stats
CREATE TABLE IF NOT EXISTS user_stats (
    id SERIAL PRIMARY KEY,
    user_id INT UNIQUE REFERENCES users(id),
    viewed INT DEFAULT 0,
    saved INT DEFAULT 0,
    reported INT DEFAULT 0,
    last_active TIMESTAMP,
    read_full_count INT DEFAULT 0,
    skipped_count INT DEFAULT 0,
    liked_count INT DEFAULT 0,
    disliked_count INT DEFAULT 0,
    comments_count INT DEFAULT 0,
    sources_added_count INT DEFAULT 0
);
-- Додавання відсутніх стовпців до таблиці user_stats
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS read_full_count INT DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS skipped_count INT DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS liked_count INT DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS disliked_count INT DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS comments_count INT DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS sources_added_count INT DEFAULT 0;


-- Додавання/оновлення таблиці subscriptions
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id INT UNIQUE REFERENCES users(id),
    active BOOLEAN DEFAULT TRUE,
    frequency TEXT DEFAULT 'daily',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Додавання відсутніх стовпців до таблиці subscriptions
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS frequency TEXT DEFAULT 'daily';


-- Додавання/оновлення таблиці summaries
CREATE TABLE IF NOT EXISTS summaries (
    id SERIAL PRIMARY KEY,
    news_id INT REFERENCES news(id) UNIQUE,
    summary TEXT,
    translated TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Додавання відсутніх стовпців до таблиці summaries
ALTER TABLE summaries ADD COLUMN IF NOT EXISTS translated TEXT;


-- Додавання/оновлення таблиці translations_cache
CREATE TABLE IF NOT EXISTS translations_cache (
    id SERIAL PRIMARY KEY,
    original_text TEXT NOT NULL,
    original_lang TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    translated_lang TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (original_text, original_lang, translated_lang)
);

-- Додавання/оновлення таблиці ratings
CREATE TABLE IF NOT EXISTS ratings (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    value INT CHECK (value BETWEEN 1 AND 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, news_id)
);

-- Додавання/оновлення таблиці blocks
CREATE TABLE IF NOT EXISTS blocks (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    block_type TEXT,
    value TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, block_type, value)
);

-- Додавання/оновлення таблиці logs
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    user_id INT,
    action TEXT,
    data JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці bookmarks
CREATE TABLE IF NOT EXISTS bookmarks (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, news_id)
);

-- Додавання/оновлення таблиці reactions
CREATE TABLE IF NOT EXISTS reactions (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    reaction_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, news_id)
);

-- Додавання/оновлення таблиці poll_results
CREATE TABLE IF NOT EXISTS poll_results (
    id SERIAL PRIMARY KEY,
    news_id INT REFERENCES news(id),
    user_id INT REFERENCES users(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (news_id, user_id, question)
);

-- Додавання/оновлення таблиці archived_news
CREATE TABLE IF NOT EXISTS archived_news (
    id SERIAL PRIMARY KEY,
    original_news_id INT UNIQUE,
    title TEXT,
    content TEXT,
    lang TEXT,
    country TEXT,
    tags TEXT[],
    source TEXT,
    link TEXT, -- Це поле має бути присутнім
    published_at TIMESTAMP,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Додавання відсутніх стовпців до таблиці archived_news
ALTER TABLE archived_news ADD COLUMN IF NOT EXISTS link TEXT;

-- Додавання/оновлення таблиці user_news_views
CREATE TABLE IF NOT EXISTS user_news_views (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    news_id INT REFERENCES news(id),
    viewed BOOLEAN DEFAULT FALSE,
    read_full BOOLEAN DEFAULT FALSE,
    first_viewed_at TIMESTAMP,
    last_viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    time_spent_seconds INT DEFAULT 0,
    UNIQUE (user_id, news_id)
);
-- Додавання відсутніх стовпців до таблиці user_news_views
ALTER TABLE user_news_views ADD COLUMN IF NOT EXISTS read_full BOOLEAN DEFAULT FALSE;
ALTER TABLE user_news_views ADD COLUMN IF NOT EXISTS first_viewed_at TIMESTAMP;
ALTER TABLE user_news_views ADD COLUMN IF NOT EXISTS last_viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE user_news_views ADD COLUMN IF NOT EXISTS time_spent_seconds INT DEFAULT 0;

-- Додавання/оновлення таблиці blocked_sources
CREATE TABLE IF NOT EXISTS blocked_sources (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id) UNIQUE NOT NULL,
    reason TEXT,
    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці comments
CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    news_id INT REFERENCES news(id),
    user_id INT REFERENCES users(id),
    parent_comment_id INT REFERENCES comments(id),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    moderated_at TIMESTAMP,
    moderation_status TEXT DEFAULT 'pending'
);
-- Додавання відсутніх стовпців до таблиці comments
ALTER TABLE comments ADD COLUMN IF NOT EXISTS moderated_at TIMESTAMP;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS moderation_status TEXT DEFAULT 'pending';

-- Додавання/оновлення таблиці invites
CREATE TABLE IF NOT EXISTS invites (
    id SERIAL PRIMARY KEY,
    inviter_user_id INT REFERENCES users(id),
    invited_user_id INT UNIQUE REFERENCES users(id),
    invite_code TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMP
);

-- Додавання/оновлення таблиці admin_actions
CREATE TABLE IF NOT EXISTS admin_actions (
    id SERIAL PRIMARY KEY,
    admin_user_id INT,
    action_type TEXT NOT NULL,
    target_id INT,
    details JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Додавання/оновлення таблиці source_stats
CREATE TABLE IF NOT EXISTS source_stats (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id) UNIQUE,
    publication_count INT DEFAULT 0,
    avg_rating REAL DEFAULT 0.0,
    report_count INT DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Створення або перестворення індексів. IF NOT EXISTS тут особливо корисний.
CREATE INDEX IF NOT EXISTS idx_news_published_expires_moderation ON news (published_at DESC, expires_at, moderation_status);
CREATE INDEX IF NOT EXISTS idx_filters_user_id ON filters (user_id);
CREATE INDEX IF NOT EXISTS idx_blocks_user_type_value ON blocks (user_id, block_type, value);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user_id ON bookmarks (user_id);
CREATE INDEX IF NOT EXISTS idx_user_stats_user_id ON user_stats (user_id);
CREATE INDEX IF NOT EXISTS idx_comments_news_id ON comments (news_id);
CREATE INDEX IF NOT EXISTS idx_user_news_views_user_news ON user_news_views (user_id, news_id);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);
