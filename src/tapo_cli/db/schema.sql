-- Idempotent schema. Re-run safe on every startup.
--
-- Credential model (modern Tapo firmware): the only secret is the TP-Link
-- *account* password. The local control API and recording downloads both log
-- in as user "admin" with that password. The Tapo app's separate "Camera
-- Account" (for RTSP) is NOT used here.

CREATE TABLE IF NOT EXISTS cameras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    slug            TEXT    NOT NULL UNIQUE,         -- download dir name
    host            TEXT    NOT NULL,                -- ip or hostname
    control_port    INTEGER,                         -- optional; pytapo default (443) if null
    account_password TEXT   NOT NULL,                -- TP-Link account password (stored unencrypted)
    enabled         INTEGER NOT NULL DEFAULT 1,      -- reserved; not currently gating anything
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

-- Single-row table: the TP-Link *cloud* account (distinct from each camera's
-- local admin/account_password, though in practice it's often the same
-- password). Only needed to opt into WebRTC playback - the existing local
-- HTTP playback/thumbnail/download features never touch this table. Stored
-- unencrypted, same rationale as cameras.account_password (personal home
-- use, local-only app).
CREATE TABLE IF NOT EXISTS cloud_account (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    email             TEXT    NOT NULL,
    password          TEXT    NOT NULL,
    terminal_uuid     TEXT    NOT NULL,          -- generated once, persisted (mimics a per-install app ID)
    token             TEXT,
    refresh_token     TEXT,
    account_id        TEXT,
    app_server_url    TEXT,                      -- region-specific server returned by login
    token_expires_at  TEXT,                      -- unix ts (as text) or null if unknown
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
