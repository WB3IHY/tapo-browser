"""Data access for cameras and download jobs (raw sqlite3)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .connection import get_conn, lock


def slugify(name: str) -> str:
    """Turn a camera name into a stream-id / directory-safe slug."""
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "camera"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Cameras
# --------------------------------------------------------------------------- #
class CameraRepo:
    def list(self) -> list[dict[str, Any]]:
        with lock():
            rows = get_conn().execute(
                "SELECT * FROM cameras ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_enabled(self) -> list[dict[str, Any]]:
        with lock():
            rows = get_conn().execute(
                "SELECT * FROM cameras WHERE enabled=1 ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, camera_id: int) -> dict[str, Any] | None:
        with lock():
            row = get_conn().execute(
                "SELECT * FROM cameras WHERE id=?", (camera_id,)
            ).fetchone()
        return _row_to_dict(row)

    def _unique_slug(self, conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> str:
        base = slugify(name)
        candidate = base
        i = 2
        while True:
            if exclude_id is None:
                row = conn.execute(
                    "SELECT 1 FROM cameras WHERE slug=?", (candidate,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM cameras WHERE slug=? AND id<>?", (candidate, exclude_id)
                ).fetchone()
            if row is None:
                return candidate
            candidate = f"{base}_{i}"
            i += 1

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        with lock():
            conn = get_conn()
            slug = self._unique_slug(conn, data["name"])
            cur = conn.execute(
                """
                INSERT INTO cameras
                    (name, slug, host, control_port, account_password, enabled)
                VALUES (:name, :slug, :host, :control_port, :account_password, :enabled)
                """,
                {
                    "name": data["name"],
                    "slug": slug,
                    "host": data["host"],
                    "control_port": data.get("control_port"),
                    "account_password": data["account_password"],
                    "enabled": 1 if data.get("enabled", True) else 0,
                },
            )
            conn.commit()
            new_id = cur.lastrowid
        return self.get(new_id)  # type: ignore[return-value]

    def update(self, camera_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get(camera_id)
        if existing is None:
            return None
        with lock():
            conn = get_conn()
            merged = {**existing, **{k: v for k, v in data.items() if v is not None}}
            # "enabled" can legitimately be set to False (falsy) — handle explicitly.
            if "enabled" in data and data["enabled"] is not None:
                merged["enabled"] = 1 if data["enabled"] else 0
            slug = existing["slug"]
            if data.get("name") and data["name"] != existing["name"]:
                slug = self._unique_slug(conn, data["name"], exclude_id=camera_id)
            conn.execute(
                """
                UPDATE cameras SET
                    name=:name, slug=:slug, host=:host, control_port=:control_port,
                    account_password=:account_password, enabled=:enabled,
                    updated_at=datetime('now')
                WHERE id=:id
                """,
                {
                    "id": camera_id,
                    "name": merged["name"],
                    "slug": slug,
                    "host": merged["host"],
                    "control_port": merged.get("control_port"),
                    "account_password": merged["account_password"],
                    "enabled": merged.get("enabled", 1),
                },
            )
            conn.commit()
        return self.get(camera_id)

    def delete(self, camera_id: int) -> bool:
        with lock():
            conn = get_conn()
            cur = conn.execute("DELETE FROM cameras WHERE id=?", (camera_id,))
            conn.commit()
            return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Cloud account (TP-Link cloud login, for WebRTC playback only)
# --------------------------------------------------------------------------- #
class CloudAccountRepo:
    def get(self) -> dict[str, Any] | None:
        with lock():
            row = get_conn().execute("SELECT * FROM cloud_account WHERE id=1").fetchone()
        return _row_to_dict(row)

    def set_credentials(self, email: str, password: str, terminal_uuid: str) -> dict[str, Any]:
        """Create or update the stored email/password. Clears any existing
        token, since credentials changing invalidates whatever session was
        tied to the old ones."""
        with lock():
            conn = get_conn()
            conn.execute(
                """
                INSERT INTO cloud_account (id, email, password, terminal_uuid)
                VALUES (1, :email, :password, :terminal_uuid)
                ON CONFLICT(id) DO UPDATE SET
                    email=:email, password=:password, terminal_uuid=:terminal_uuid,
                    token=NULL, refresh_token=NULL, account_id=NULL,
                    app_server_url=NULL, token_expires_at=NULL,
                    updated_at=datetime('now')
                """,
                {"email": email, "password": password, "terminal_uuid": terminal_uuid},
            )
            conn.commit()
        return self.get()  # type: ignore[return-value]

    def set_session(
        self,
        token: str,
        refresh_token: str | None,
        account_id: str | None,
        app_server_url: str | None,
        token_expires_at: str | None,
    ) -> None:
        with lock():
            conn = get_conn()
            conn.execute(
                """
                UPDATE cloud_account SET
                    token=:token, refresh_token=:refresh_token, account_id=:account_id,
                    app_server_url=:app_server_url, token_expires_at=:token_expires_at,
                    updated_at=datetime('now')
                WHERE id=1
                """,
                {
                    "token": token,
                    "refresh_token": refresh_token,
                    "account_id": account_id,
                    "app_server_url": app_server_url,
                    "token_expires_at": token_expires_at,
                },
            )
            conn.commit()

    def clear(self) -> None:
        with lock():
            conn = get_conn()
            conn.execute("DELETE FROM cloud_account WHERE id=1")
            conn.commit()


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
class DownloadRepo:
    def create(self, camera_id: int, date: str, start_time: int, end_time: int) -> dict[str, Any]:
        with lock():
            conn = get_conn()
            cur = conn.execute(
                """
                INSERT INTO downloads (camera_id, date, start_time, end_time, status, total_sec)
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (camera_id, date, start_time, end_time, max(0, end_time - start_time)),
            )
            conn.commit()
            new_id = cur.lastrowid
        return self.get(new_id)  # type: ignore[return-value]

    def get(self, download_id: int) -> dict[str, Any] | None:
        with lock():
            row = get_conn().execute(
                "SELECT * FROM downloads WHERE id=?", (download_id,)
            ).fetchone()
        return _row_to_dict(row)

    def list(self, camera_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with lock():
            if camera_id is None:
                rows = get_conn().execute(
                    "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = get_conn().execute(
                    "SELECT * FROM downloads WHERE camera_id=? ORDER BY id DESC LIMIT ?",
                    (camera_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def update(self, download_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with lock():
            conn = get_conn()
            conn.execute(
                f"UPDATE downloads SET {cols}, updated_at=datetime('now') WHERE id=?",
                (*fields.values(), download_id),
            )
            conn.commit()

    def reset_stale(self) -> None:
        """On startup, any 'running'/'queued' job from a previous run is dead."""
        with lock():
            conn = get_conn()
            conn.execute(
                "UPDATE downloads SET status='error', error='Interrupted by app restart', "
                "updated_at=datetime('now') WHERE status IN ('running','queued')"
            )
            conn.commit()
