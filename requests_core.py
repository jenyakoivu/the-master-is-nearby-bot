"""Общая логика заявок: пинги мастерам в чат, уведомления клиенту.
Карточки с кнопками в чате больше не используются — вся работа в мини-аппах.
В чат мастер-бота идёт только короткий ПИНГ о свободной заявке, который удаляется,
как только заявка перестаёт быть свободной."""

import html
import logging

import config
import database

logger = logging.getLogger(__name__)


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
        f"📍 {html.escape(req['district'])} · ⏱ {html.escape(req['urgency'])}\n"
        f"Откройте «Доску заявок», чтобы взять."
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
            f"Спасибо, что выбрали «Техник Рядом»! 💖"
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
            f"Спасибо, что выбрали «Техник Рядом»! 💖"
        )
    else:
        return
    try:
        await client_bot.send_message(chat_id=client_id, text=text, parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось уведомить клиента %s", client_id)


async def notify_master_canceled(master_bot, master_id: int, req: dict) -> None:
    """Личное уведомление мастеру: клиент отменил уже взятую им заявку."""
    try:
        await master_bot.send_message(
            chat_id=master_id,
            text=(
                f"❌ <b>Клиент отменил заявку №{req['id']}</b>\n"
                f"«{html.escape(req['problem'])}» — больше выполнять не нужно."
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("Не удалось уведомить мастера об отмене %s", master_id)
