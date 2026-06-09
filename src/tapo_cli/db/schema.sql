-- Idempotent schema. Re-run safe on every startup.
--
-- Credential model (modern Tapo firmware): the only secret is the TP-Link
-- *account* password. The local control API logs in as user "admin" with that
-- password; go2rtc's native tapo:// stream source uses SHA256(account_password).
-- The Tapo app's separate "Camera Account" (for RTSP) is NOT used here.

CREATE TABLE IF NOT EXISTS cameras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL UNIQUE,         -- go2rtc stream id + download dir name
    host            TEXT    NOT NULL,                -- ip or hostname
    control_port    INTEGER,                         -- optional; pytapo/go2rtc default (443) if null
    account_password TEXT   NOT NULL,                -- TP-Link account password (stored unencrypted)
    enabled         INTEGER NOT NULL DEFAULT 1,      -- include in go2rtc config
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS downloads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id      INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    date           TEXT    NOT NULL,               -- YYYYMMDD
    start_time     INTEGER NOT NULL,               -- unix ts of segment start
    end_time       INTEGER NOT NULL,               -- unix ts of segment end
    status         TEXT    NOT NULL DEFAULT 'queued', -- queued|running|done|error|canceled
    current_action TEXT,
    progress_sec   REAL    NOT NULL DEFAULT 0,
    total_sec      REAL    NOT NULL DEFAULT 0,
    file_path      TEXT,
    error          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_downloads_camera ON downloads(camera_id, date);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
