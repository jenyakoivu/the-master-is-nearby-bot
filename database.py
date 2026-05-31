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


def get_user_requests(user_id: int, limit: int = 10) -> list[dict]:
    """Заявки конкретного клиента (для раздела «Мои заявки»)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, phone, status
            FROM requests
            WHERE user_id = ? AND status != 'canceled'
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def cancel_request(request_id: int, user_id: int) -> bool:
    """Клиент отменяет свою заявку (помечает 'canceled', из базы не удаляет).
    Отменить может только владелец заявки. Возвращает True, если получилось."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE requests
            SET status = 'canceled'
            WHERE id = ? AND user_id = ? AND status != 'canceled'
            """,
            (request_id, user_id),
        )
        return cursor.rowcount > 0


# Поля, которые клиент может менять в зависимости от статуса заявки
EDITABLE_WHEN_NEW = ("problem", "district", "address", "urgency", "phone")
EDITABLE_WHEN_TAKEN = ("address", "phone")


def update_request(request_id: int, user_id: int, fields: dict) -> str | None:
    """Редактирование заявки клиентом с учётом статуса.
    Возвращает новый статус заявки ('new'/'taken') при успехе, иначе None.
    Пока 'new' — можно менять всё разрешённое; когда 'taken' — только адрес/телефон."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM requests WHERE id = ? AND user_id = ?",
            (request_id, user_id),
        ).fetchone()
        if not row:
            return None
        status = row["status"]
        if status == "new":
            allowed = EDITABLE_WHEN_NEW
        elif status == "taken":
            allowed = EDITABLE_WHEN_TAKEN
        else:
            return None  # canceled — редактировать нельзя

        updates = {k: v for k, v in fields.items() if k in allowed and v is not None and str(v).strip()}
        if not updates:
            return status  # нечего менять — не ошибка

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [request_id, user_id]
        conn.execute(
            f"UPDATE requests SET {set_clause} WHERE id = ? AND user_id = ?",
            values,
        )
        return status


def complete_request(request_id: int, master_id: int) -> bool:
    """Мастер отмечает свою заявку выполненной. Только взявший может завершить."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE requests
            SET status = 'done'
            WHERE id = ? AND status = 'taken' AND taken_by_id = ?
            """,
            (request_id, master_id),
        )
        return cursor.rowcount > 0


def get_open_requests(limit: int = 50) -> list[dict]:
    """Доска заявок: только свободные (open/new). Телефон не отдаём наружу здесь."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, status
            FROM requests
            WHERE status = 'new'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_master_active(master_id: int) -> list[dict]:
    """Заявки мастера в работе (taken им)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, phone,
                   username, full_name, status
            FROM requests
            WHERE status = 'taken' AND taken_by_id = ?
            ORDER BY id DESC
            """,
            (master_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_master_history(master_id: int, limit: int = 50) -> list[dict]:
    """История мастера: выполненные им (done) и отменённые клиентом заявки,
    которые были взяты ИМ. Переданные сюда не попадают (taken_by_id обнуляется при передаче)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, problem, district, address, urgency, phone,
                   username, full_name, status
            FROM requests
            WHERE taken_by_id = ? AND status IN ('done', 'canceled')
            ORDER BY id DESC
            LIMIT ?
            """,
            (master_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
