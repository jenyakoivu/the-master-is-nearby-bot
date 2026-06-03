"""Общая логика заявок: пинги мастерам в чат, уведомления клиенту.
Карточки с кнопками в чате больше не используются — вся работа в мини-аппах.
В чат мастер-бота идёт только короткий ПИНГ о свободной заявке, который удаляется,
как только заявка перестаёт быть свободной."""

import html
import logging

import config
import database

logger = logging.getLogger(__name__)


def normalize_ru_phone(raw: str) -> str | None:
    """Приводит РФ-номер к виду +7XXXXXXXXXX. Возвращает None, если номер некорректен."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    # 8XXXXXXXXXX -> 7XXXXXXXXXX
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    # 9XXXXXXXXX (10 цифр без кода страны) -> 7 + ...
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "7":
        return "+" + digits
    return None


def format_ru_phone(raw: str) -> str:
    """Красивый показ: +7 999 123-45-67. Если номер нестандартный — возвращает как есть."""
    norm = normalize_ru_phone(raw)
    if not norm:
        return raw
    d = norm[2:]  # 10 цифр после +7
    return f"+7 {d[0:3]} {d[3:6]}-{d[6:8]}-{d[8:10]}"


def phone_for_dial(raw: str) -> str:
    """Чистый номер для ссылки tel: только + и цифры."""
    return normalize_ru_phone(raw) or ("+" + "".join(ch for ch in (raw or "") if ch.isdigit()))


def mask_phone(phone: str) -> str:
    prefix = "+" if phone.strip().startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    visible = digits[:4]
    hidden = "✱" * max(len(digits) - 4, 4)
    return prefix + visible + hidden


def _ping_text(req: dict) -> str:
    """Короткий пинг: минимум инфы, без контактов и без кнопок."""
    return (
        f"🆕 <b>Новая заявка №{req['id']}</b>\n"
        f"📍 — {html.escape(req['district'])} · ⏱ — {html.escape(req['urgency'])}\n"
        f"Откройте «Доску заявок» в кабинете мастера, чтобы принять."
    )


async def send_ping(master_bot, request_id: int) -> None:
    """Шлёт пинг о свободной заявке всем мастерам и запоминает id сообщений."""
    req = database.get_request(request_id)
    if not req or req["status"] != "new":
        return
    text = _ping_text(req)
    for master_id in config.MASTER_IDS:
        try:
            msg = await master_bot.send_message(chat_id=master_id, text=text, parse_mode="HTML")
            database.save_master_message(request_id, int(master_id), msg.message_id)
        except Exception:
            logger.warning("Не удалось отправить пинг мастеру %s", master_id)


async def remove_ping(master_bot, request_id: int) -> None:
    """Удаляет пинги о заявке из чатов всех мастеров (заявка больше не свободна)."""
    for master_id, message_id in database.get_master_messages(request_id):
        try:
            await master_bot.delete_message(chat_id=master_id, message_id=message_id)
        except Exception:
            logger.debug("Не удалось удалить пинг у мастера %s", master_id)
    database.clear_master_messages(request_id)


# Совместимость со старым кодом, который вызывал broadcast_new_request/broadcast_update.
async def broadcast_new_request(master_bot, request_id: int) -> None:
    await send_ping(master_bot, request_id)


async def broadcast_update(master_bot, req: dict) -> None:
    """Заявка изменила статус: если снова свободна — пингуем, иначе убираем пинг."""
    if req["status"] == "new":
        # сначала уберём старые пинги (если были), потом отправим свежий
        await remove_ping(master_bot, req["id"])
        await send_ping(master_bot, req["id"])
    else:
        await remove_ping(master_bot, req["id"])


async def notify_client(client_bot, req: dict, event: str) -> None:
    """Уведомления клиенту об изменении статуса его заявки."""
    client_id = req.get("user_id")
    if not client_id:
        return
    rid = req["id"]
    if event == "taken":
        text = (
            f"✅ <b>Мастер найден!</b>\n\n"
            f"По заявке №{rid} («{html.escape(req['problem'])}») назначен мастер. "
            f"Он свяжется с вами в ближайшее время.\n\n"
            f"Спасибо, что выбрали «Мастер Рядом»! 💖"
        )
    elif event == "released":
        text = (
            f"🔄 По заявке №{rid} («{html.escape(req['problem'])}») снова ищем мастера.\n"
            f"Как только кто-то возьмёт заявку, мы сообщим."
        )
    elif event == "done":
        text = (
            f"✅ <b>Заявка №{rid} выполнена!</b>\n\n"
            f"Работа по заявке («{html.escape(req['problem'])}») завершена.\n"
            f"Спасибо, что выбрали «Мастер Рядом»! 💖"
        )
    else:
        return
    try:
        await client_bot.send_message(chat_id=client_id, text=text, parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось уведомить клиента %s", client_id)


async def notify_master_canceled(master_bot, master_id: int, req: dict) -> None:
    """Личное уведомление мастеру: клиент отменил уже взятую им заявку.
    Запоминаем id сообщения и вешаем кнопку «Удалить из истории»."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑 Удалить из истории", callback_data=f"delhist:{req['id']}")]]
    )
    try:
        msg = await master_bot.send_message(
            chat_id=master_id,
            text=(
                f"❌ <b>Клиент отменил заявку №{req['id']}</b>\n"
                f"«{html.escape(req['problem'])}» — заявка больше не актуальна."
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
        database.save_cancel_notice(req["id"], master_id, msg.message_id)
    except Exception:
        logger.warning("Не удалось уведомить мастера об отмене %s", master_id)


# ---------- Сообщение-статус заявки в чате клиента ----------
# Одна заявка = одно сообщение. При смене статуса старое удаляется, новое присылается.

def _client_status_text(req: dict) -> str:
    status = req["status"]
    extra = ""
    if status == "new":
        if req.get("released_once"):
            status_line = "🔍 Ищем другого мастера"
        else:
            status_line = "🔍 Ищем мастера"
    elif status == "taken":
        status_line = "✅ Нашли для вас мастера"
        extra = "\n\nМастер скоро свяжется с вами ✨"
    elif status == "done":
        status_line = "🏁 Заявка выполнена"
    else:
        status_line = status
    return (
        f"<b>Заявка №{req['id']}</b> · {status_line}\n\n"
        f"🛠 — {html.escape(req['problem'])}\n"
        f"📍 — {html.escape(req['district'])}, {html.escape(req['address'] or '—')}\n"
        f"⏱ — {html.escape(req['urgency'])}\n"
        f"📞 — {html.escape(format_ru_phone(req['phone']))}"
        + extra
    )


async def refresh_client_status(client_bot, req: dict) -> None:
    """Обновляет сообщение-статус заявки у клиента: удаляет старое, шлёт новое.
    Для 'canceled' — просто удаляет. Для 'done' — добавляет кнопку «Удалить»."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    client_id = req.get("user_id")
    if not client_id:
        return
    rid = req["id"]

    # Удаляем предыдущее сообщение-статус, если было
    prev = database.get_client_status_message(rid)
    if prev:
        chat_id, message_id = prev
        try:
            await client_bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        database.delete_client_status_message(rid)

    # Отменённая заявка — сообщение не пересоздаём
    if req["status"] == "canceled":
        return

    kb = None
    if req["status"] == "done":
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑 Удалить", callback_data=f"cstatus_del:{rid}")]]
        )
    try:
        msg = await client_bot.send_message(
            chat_id=client_id, text=_client_status_text(req),
            parse_mode="HTML", reply_markup=kb,
        )
        database.save_client_status_message(rid, client_id, msg.message_id)
    except Exception:
        logger.warning("Не удалось отправить статус клиенту %s", client_id)
