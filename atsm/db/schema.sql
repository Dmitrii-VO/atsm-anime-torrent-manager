-- ATSM, версия схемы 1 (ТЗ §18).
-- Дедупликация раздач: UNIQUE(anime_id, external_id), где external_id —
-- идентификатор раздачи на сайте. Он стабилен, не зависит от заголовка
-- и переживает смену домена источника.

CREATE TABLE IF NOT EXISTS anime (
    id            INTEGER PRIMARY KEY,
    title         TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    url           TEXT    NOT NULL UNIQUE,
    slug          TEXT    NOT NULL,
    poster_path   TEXT,
    status        TEXT    NOT NULL DEFAULT 'ongoing',   -- ongoing|completed|paused
    auto_download INTEGER NOT NULL DEFAULT 0,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    last_check_at TEXT,
    last_check_ok INTEGER,
    last_error    TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anime_source ON anime(source);
CREATE INDEX IF NOT EXISTS idx_anime_slug   ON anime(source, slug);

CREATE TABLE IF NOT EXISTS release (
    id            INTEGER PRIMARY KEY,
    anime_id      INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    external_id   TEXT    NOT NULL,
    episode       INTEGER,                               -- NULL, если не распознан
    episode_raw   TEXT    NOT NULL,
    title         TEXT,
    quality       TEXT,                                  -- задел под источники с качеством
    size_bytes    INTEGER,
    seeders       INTEGER,
    leechers      INTEGER,
    downloads     INTEGER,
    published_at  TEXT,
    torrent_url   TEXT,
    magnet        TEXT,                                  -- NULL для astar.bz
    info_hash     TEXT,                                  -- для сверки статуса в клиенте
    state         TEXT    NOT NULL DEFAULT 'new',        -- new|sent|downloaded|error|ignored
    state_message TEXT,
    is_seen       INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT    NOT NULL,
    updated_at    TEXT,
    UNIQUE(anime_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_release_anime ON release(anime_id, episode DESC);
CREATE INDEX IF NOT EXISTS idx_release_feed  ON release(is_seen, state, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY,
    anime_id   INTEGER REFERENCES anime(id)   ON DELETE CASCADE,
    release_id INTEGER REFERENCES release(id) ON DELETE CASCADE,
    action     TEXT NOT NULL,   -- found|sent|downloaded|send_error|parse_error|check
    message    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_anime   ON history(anime_id, created_at DESC);

-- Диагностика источников (ТЗ §21).
CREATE TABLE IF NOT EXISTS source_status (
    source     TEXT PRIMARY KEY,
    state      TEXT NOT NULL,   -- ok|unreachable|parse_error|layout_changed
    message    TEXT,
    checked_at TEXT
);

-- Правила выбора релизов — задел под версию 2 (ТЗ §9).
CREATE TABLE IF NOT EXISTS rules (
    id        INTEGER PRIMARY KEY,
    anime_id  INTEGER REFERENCES anime(id) ON DELETE CASCADE,  -- NULL = глобальное
    kind      TEXT    NOT NULL,   -- prefer|exclude
    pattern   TEXT    NOT NULL,
    priority  INTEGER NOT NULL DEFAULT 0,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
