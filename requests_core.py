"""Общая логика заявок: карточки для мастеров, маскировка телефона, рассылка.
Используется мастерским ботом. База — общая (database.py)."""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
import database

logger = logging.getLogger(__name__)


def mask_phone(phone: str) -> str:
    """Показывает первые 4 ЦИФРЫ номера, остальные цифры скрывает звёздочками."""
    prefix = "+" if phone.strip().startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    visible = digits[:4]
    hidden = "✱" * max(len(digits) - 4, 4)
    return prefix + visible + hidden


def client_contact(req: dict) -> str:
    """Имя клиента + кликабельный @username (если есть) для связи в Telegram."""
    name = html.escape(req["full_name"] or "—")
    if req.get("username"):
        return f"{name} (@{req['username']})"
    return name


def _base_lines(req: dict) -> str:
    return (
        f"🛠 Проблема: {html.escape(req['problem'])}\n"
        f"📍 Район: {html.escape(req['district'])}\n"
        f"🏠 Адрес: {html.escape(req['address'] or '—')}\n"
        f"⏱ Срочность: {html.escape(req['urgency'])}"
    )


def card_free(req: dict):
    text = (
        f"🆕 <b>Заявка №{req['id']}</b> · 🟢 ОТКРЫТА\n\n"
        f"{_base_lines(req)}\n"
        f"📱 {mask_phone(req['phone'])}"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✋ Взять заявку", callback_data=f"take:{req['id']}")]]
    )
    return text, kb


def card_taken_owner(req: dict):
    text = (
        f"🆕 <b>Заявка №{req['id']}</b> · ✅ ПРИНЯТА (ваша)\n\n"
        f"{_base_lines(req)}\n"
        f"📱 Телефон: {html.escape(req['phone'])}\n"
        f"👤 Клиент: {client_contact(req)}"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Передать заявку", callback_data=f"release:{req['id']}")]]
    )
    return text, kb


def card_taken_other(req: dict):
    text = (
        f"🔴 <b>Заявка №{req['id']}</b> · ЗАНЯТА — {html.escape(req['taken_by'] or '')}\n\n"
        f"{_base_lines(req)}\n"
        f"📱 Заявку взял другой мастер"
    )
    return text, None


def card_canceled(req: dict):
    text = (
        f"❌ <b>Заявка №{req['id']}</b> · ОТМЕНЕНА клиентом\n\n"
        f"{_base_lines(req)}"
    )
    return text, None


def master_display_name(user) -> str:
    return f"@{user.username}" if user.username else (user.full_name or f"id {user.id}")


async def broadcast_new_request(master_bot, request_id: int) -> None:
    """Рассылает новую заявку всем мастерам через мастерский бот и запоминает id сообщений."""
    req = database.get_request(request_id)
    if not req:
        return
    text, kb = card_free(req)
    for master_id in config.MASTER_IDS:
        try:
            msg = await master_bot.send_message(
                chat_id=master_id, text=text, parse_mode="HTML", reply_markup=kb
            )
            database.save_master_message(request_id, int(master_id), msg.message_id)
        except Exception:
            logger.warning("Не удалось отправить заявку мастеру %s", master_id)


async def broadcast_update(master_bot, req: dict) -> None:
    """Обновляет все копии заявки у мастеров согласно текущему статусу."""
    rid = req["id"]
    for master_id, message_id in database.get_master_messages(rid):
        if req["status"] == "canceled":
            text, kb = card_canceled(req)
        elif req["status"] == "new":
            text, kb = card_free(req)
        elif master_id == req["taken_by_id"]:
            text, kb = card_taken_owner(req)
        else:
            text, kb = card_taken_other(req)
        try:
            await master_bot.edit_message_text(
                chat_id=master_id, message_id=message_id,
                text=text, parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            logger.debug("Не удалось обновить сообщение мастера %s", master_id)


async def notify_client(client_bot, req: dict, event: str) -> None:
    """Уведомляет клиента об изменении статуса его заявки.
    event: 'taken' — мастер взял; 'released' — мастер отказался, снова ищем."""
    client_id = req.get("user_id")
    if not client_id:
        return
    rid = req["id"]
    if event == "taken":
        text = (
            f"✅ <b>Мастер найден!</b>\n\n"
            f"По заявке №{rid} («{html.escape(req['problem'])}») назначен мастер. "
            f"Он свяжется с вами в ближайшее время.\n\n"
            f"Спасибо, что выбрали «Сантехник Рядом»! 💖"
        )
    elif event == "released":
        text = (
            f"🔄 По заявке №{rid} («{html.escape(req['problem'])}») снова ищем мастера.\n"
            f"Как только кто-то возьмёт заявку, мы сообщим."
        )
    else:
        return
    try:
        await client_bot.send_message(chat_id=client_id, text=text, parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось уведомить клиента %s", client_id)
