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
        # Скрытые из истории мастера заявки (мастер «очистил» свой вид; данные в базе целы)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hidden_history (
                master_id   INTEGER NOT NULL,
                request_id  INTEGER NOT NULL,
                PRIMARY KEY (master_id, request_id)
            )
            """
        )
        # Сообщения-уведомления мастеру об отмене (чтобы можно было удалить из чата)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cancel_notices (
                request_id  INTEGER NOT NULL,
                master_id   INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                PRIMARY KEY (request_id, master_id)
            )
            """
        )
        # Сообщение-статус заявки в чате клиента (одно на заявку; при смене статуса пересоздаётся)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_status_messages (
                request_id  INTEGER PRIMARY KEY,
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL
            )
            """
        )
        # Отметка, что заявку хотя бы раз передавали (для статуса «снова ищем»)
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN released_once INTEGER DEFAULT 0")
        except Exception:
            pass
        # Источник заявки: tg (Telegram) или vk (ВКонтакте)
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN source TEXT DEFAULT 'tg'")
        except Exception:
            pass
        # ВК: кто разрешил сообщения от сообщества
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_allowed (
                vk_id INTEGER PRIMARY KEY,
                allowed INTEGER DEFAULT 1
            )
            """
        )
        # ВК: сообщение-статус заявки в личке клиента (одно на заявку, как в ТГ)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_client_status (
                request_id INTEGER PRIMARY KEY,
                vk_id      INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )
        # ВК: пинги мастерам о заявке (как master_messages в ТГ)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_master_pings (
                request_id INTEGER NOT NULL,
                vk_id      INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (request_id, vk_id)
            )
            """
        )
        # ВК: уведомления мастеру об отмене (для кнопки «Убрать»)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_cancel_notices (
                request_id INTEGER NOT NULL,
                vk_id      INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (request_id, vk_id)
            )
            """
        )
        # ВК: фото и отображаемое имя клиента (для показа мастеру)
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN client_photo TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN client_name TEXT DEFAULT ''")
        except Exception:
            pass
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


def save_request(data: dict, user, source: str = "tg") -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO requests
                (created_at, problem, district, address, urgency, phone,
                 user_id, username, full_name, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
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
                source,
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
            SET status = 'new', taken_by_id = NULL, taken_by = NULL, released_once = 1
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
            SELECT id, created_at, problem, district, address, urgency, phone, status, released_once
            FROM requests
            WHERE user_id = ? AND status IN ('new', 'taken')
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


def get_master_active(master_id) -> list[dict]:
    """Заявки мастера в работе (taken им). master_id может быть числом или списком
    связанных ID (ТГ+ВК одного мастера)."""
    ids = master_id if isinstance(master_id, (list, tuple)) else [master_id]
    ids = [str(i) for i in ids]
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, problem, district, address, urgency, phone,
                   username, full_name, status, user_id, source, client_photo, client_name
            FROM requests
            WHERE status = 'taken' AND CAST(taken_by_id AS TEXT) IN ({placeholders})
            ORDER BY id DESC
            """,
            ids,
        ).fetchall()
        return [dict(r) for r in rows]


def get_master_history(master_id, limit: int = 50) -> list[dict]:
    """История мастера: выполненные им (done) и отменённые заявки, взятые ИМ.
    master_id может быть числом или списком связанных ID (ТГ+ВК одного мастера)."""
    ids = master_id if isinstance(master_id, (list, tuple)) else [master_id]
    ids = [str(i) for i in ids]
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, problem, district, address, urgency, phone,
                   username, full_name, status, user_id, source, client_photo, client_name
            FROM requests
            WHERE CAST(taken_by_id AS TEXT) IN ({placeholders}) AND status IN ('done', 'canceled')
              AND id NOT IN (SELECT request_id FROM hidden_history WHERE CAST(master_id AS TEXT) IN ({placeholders}))
            ORDER BY id DESC
            LIMIT ?
            """,
            ids + ids + [limit],
        ).fetchall()
        return [dict(r) for r in rows]


def clear_master_history(master_id) -> int:
    """Прячет из истории мастера все его завершённые/отменённые заявки.
    master_id может быть числом или списком связанных ID.
    Данные из базы НЕ удаляются — только перестают показываться. Возвращает число скрытых."""
    ids = master_id if isinstance(master_id, (list, tuple)) else [master_id]
    ids = [str(i) for i in ids]
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id FROM requests
            WHERE CAST(taken_by_id AS TEXT) IN ({placeholders}) AND status IN ('done', 'canceled')
            """,
            ids,
        ).fetchall()
        rid_list = [r["id"] for r in rows]
        for rid in rid_list:
            for mid in ids:
                conn.execute(
                    "INSERT OR IGNORE INTO hidden_history (master_id, request_id) VALUES (?, ?)",
                    (mid, rid),
                )
        return len(rid_list)


def clear_master_messages(request_id: int) -> None:
    """Удаляет записи об отправленных мастерам сообщениях (пингах) по заявке."""
    with _connect() as conn:
        conn.execute("DELETE FROM master_messages WHERE request_id = ?", (request_id,))


def save_cancel_notice(request_id: int, master_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cancel_notices (request_id, master_id, message_id) VALUES (?, ?, ?)",
            (request_id, master_id, message_id),
        )


def get_cancel_notices_for_master(master_id: int) -> list[tuple[int, int]]:
    """Все уведомления об отмене для мастера: список (request_id, message_id)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT request_id, message_id FROM cancel_notices WHERE master_id = ?",
            (master_id,),
        ).fetchall()
        return [(r["request_id"], r["message_id"]) for r in rows]


def get_cancel_notice(request_id: int, master_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT message_id FROM cancel_notices WHERE request_id = ? AND master_id = ?",
            (request_id, master_id),
        ).fetchone()
        return row["message_id"] if row else None


def delete_cancel_notice(request_id: int, master_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM cancel_notices WHERE request_id = ? AND master_id = ?",
            (request_id, master_id),
        )


def hide_one_from_history(master_id: int, request_id: int) -> None:
    """Прячет одну заявку из истории мастера (данные в базе остаются)."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO hidden_history (master_id, request_id) VALUES (?, ?)",
            (master_id, request_id),
        )


def save_client_status_message(request_id: int, chat_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO client_status_messages (request_id, chat_id, message_id) VALUES (?, ?, ?)",
            (request_id, chat_id, message_id),
        )


def get_client_status_message(request_id: int):
    with _connect() as conn:
        row = conn.execute(
            "SELECT chat_id, message_id FROM client_status_messages WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return (row["chat_id"], row["message_id"]) if row else None


def delete_client_status_message(request_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM client_status_messages WHERE request_id = ?", (request_id,))


# ===== ВК: разрешения на сообщения и id сообщений =====

def vk_set_allowed(vk_id: int, allowed: bool = True) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO vk_allowed (vk_id, allowed) VALUES (?, ?)",
                     (vk_id, 1 if allowed else 0))


def vk_is_allowed(vk_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT allowed FROM vk_allowed WHERE vk_id = ?", (vk_id,)).fetchone()
        return bool(row and row["allowed"])


def vk_save_client_status(request_id: int, vk_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO vk_client_status (request_id, vk_id, message_id) VALUES (?, ?, ?)",
                     (request_id, vk_id, message_id))


def vk_get_client_status(request_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT vk_id, message_id FROM vk_client_status WHERE request_id = ?", (request_id,)).fetchone()
        return (row["vk_id"], row["message_id"]) if row else None


def vk_delete_client_status(request_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM vk_client_status WHERE request_id = ?", (request_id,))


def vk_save_master_ping(request_id: int, vk_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO vk_master_pings (request_id, vk_id, message_id) VALUES (?, ?, ?)",
                     (request_id, vk_id, message_id))


def vk_get_master_pings(request_id: int) -> list[tuple[int, int]]:
    with _connect() as conn:
        rows = conn.execute("SELECT vk_id, message_id FROM vk_master_pings WHERE request_id = ?", (request_id,)).fetchall()
        return [(r["vk_id"], r["message_id"]) for r in rows]


def vk_clear_master_pings(request_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM vk_master_pings WHERE request_id = ?", (request_id,))


def set_client_info(request_id: int, photo: str, name: str) -> None:
    """Сохраняет фото и имя клиента (из ВК) для заявки."""
    with _connect() as conn:
        conn.execute("UPDATE requests SET client_photo = ?, client_name = ? WHERE id = ?",
                     (photo or "", name or "", request_id))


def vk_save_cancel_notice(request_id: int, vk_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO vk_cancel_notices (request_id, vk_id, message_id) VALUES (?, ?, ?)",
                     (request_id, vk_id, message_id))


def vk_get_cancel_notice(request_id: int, vk_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT message_id FROM vk_cancel_notices WHERE request_id = ? AND vk_id = ?",
                           (request_id, vk_id)).fetchone()
        return row["message_id"] if row else None


def vk_delete_cancel_notice(request_id: int, vk_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM vk_cancel_notices WHERE request_id = ? AND vk_id = ?", (request_id, vk_id))
