"""Хранение заявок в SQLite (requests.db)."""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "requests.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT    NOT NULL,
                problem     TEXT    NOT NULL,
                district    TEXT    NOT NULL,
                address     TEXT,
                urgency     TEXT    NOT NULL,
                phone       TEXT    NOT NULL,
                user_id     INTEGER,
                username    TEXT,
                full_name   TEXT,
                status      TEXT    DEFAULT 'new',
                taken_by_id INTEGER,
                taken_by    TEXT
            )
            """
        )
        # Таблица: какое сообщение бот отправил какому мастеру по каждой заявке.
        # Нужна, чтобы потом синхронно обновлять все копии (у взявшего и у остальных).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS master_messages (
                request_id  INTEGER NOT NULL,
                master_id   INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                PRIMARY KEY (request_id, master_id)
            )
            """
        )
        # Миграции для баз, созданных ранними версиями.
        columns = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        for col, ddl in (
            ("address", "ALTER TABLE requests ADD COLUMN address TEXT"),
            ("status", "ALTER TABLE requests ADD COLUMN status TEXT DEFAULT 'new'"),
            ("taken_by_id", "ALTER TABLE requests ADD COLUMN taken_by_id INTEGER"),
            ("taken_by", "ALTER TABLE requests ADD COLUMN taken_by TEXT"),
        ):
            if col not in columns:
                conn.execute(ddl)


def save_request(data: dict, user) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO requests
                (created_at, problem, district, address, urgency, phone,
                 user_id, username, full_name, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                data["problem"],
                data["district"],
                data.get("address", ""),
                data["urgency"],
                data["phone"],
                user.id,
                user.username,
                user.full_name,
            ),
        )
        return cursor.lastrowid


def get_request(request_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None


def take_request(request_id: int, master_id: int, master_name: str) -> bool:
    """Закрепляет заявку за мастером, если она свободна. Защита от двойного клика."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE requests
            SET status = 'taken', taken_by_id = ?, taken_by = ?
            WHERE id = ? AND status = 'new'
            """,
            (master_id, master_name, request_id),
        )
        return cursor.rowcount > 0


def release_request(request_id: int, master_id: int) -> bool:
    """Возвращает заявку в 'new'. Только тот, кто взял, может передать."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE requests
            SET status = 'new', taken_by_id = NULL, taken_by = NULL
            WHERE id = ? AND status = 'taken' AND taken_by_id = ?
            """,
            (request_id, master_id),
        )
        return cursor.rowcount > 0


def save_master_message(request_id: int, master_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO master_messages (request_id, master_id, message_id)
            VALUES (?, ?, ?)
            """,
            (request_id, master_id, message_id),
        )


def get_master_messages(request_id: int) -> list[tuple[int, int]]:
    """Возвращает список (master_id, message_id) для заявки."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT master_id, message_id FROM master_messages WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        return [(r["master_id"], r["message_id"]) for r in rows]


def get_stats() -> tuple[int, list[tuple]]:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        recent = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, phone, status, taken_by
            FROM requests
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        return total, [tuple(r) for r in recent]
