"""Хранение заявок в локальной базе SQLite (requests.db)."""

import sqlite3
from datetime import datetime
from pathlib import Path

# База лежит рядом с кодом — в той же папке, где запускается бот.
DB_PATH = Path(__file__).resolve().parent / "requests.db"


def init_db() -> None:
    """Создаёт таблицу заявок, если её нет, и добавляет недостающие колонки."""
    with sqlite3.connect(DB_PATH) as conn:
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
                full_name   TEXT
            )
            """
        )
        # Миграция: если база была создана старой версией без колонки address — добавим её.
        columns = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        if "address" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN address TEXT")


def save_request(data: dict, user) -> int:
    """Сохраняет заявку и возвращает её номер (id)."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO requests
                (created_at, problem, district, address, urgency, phone,
                 user_id, username, full_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def get_stats() -> tuple[int, list[tuple]]:
    """Возвращает (всего заявок, последние 5 заявок) для команды /stats."""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        recent = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, phone
            FROM requests
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        return total, recent
