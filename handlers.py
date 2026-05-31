"""Логика диалога: приём заявки, рассылка мастерам, статусы и кнопки взять/передать."""

import html
import logging
import re
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database
from keyboards import (
    district_keyboard,
    main_menu_keyboard,
    phone_keyboard,
    urgency_keyboard,
)

logger = logging.getLogger(__name__)

PROBLEM, DISTRICT, ADDRESS, URGENCY, PHONE = range(5)


def is_valid_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return 10 <= len(digits) <= 15


def master_display_name(user) -> str:
    return f"@{user.username}" if user.username else (user.full_name or f"id {user.id}")


# ---------- Карточки заявки для мастеров ----------
# Три варианта:
#   free          — заявка свободна: телефон скрыт, кнопка «Взять» (видят все)
#   taken_owner   — её взял этот мастер: телефон виден, кнопка «Передать»
#   taken_other   — её взял кто-то другой: телефон скрыт, без кнопок

def _base_lines(req: dict) -> str:
    return (
        f"🛠 Проблема: {html.escape(req['problem'])}\n"
        f"📍 Район: {html.escape(req['district'])}\n"
        f"🏠 Адрес: {html.escape(req['address'] or '—')}\n"
        f"⏱ Срочность: {html.escape(req['urgency'])}"
    )


def mask_phone(phone: str) -> str:
    """Показывает первые 4 ЦИФРЫ номера, остальные цифры скрывает звёздочками.
    Ведущий + сохраняется отдельно (не считается цифрой)."""
    prefix = "+" if phone.strip().startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    visible = digits[:4]
    hidden = "✱" * max(len(digits) - 4, 4)
    return prefix + visible + hidden


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
        f"✅ <b>Заявка №{req['id']}</b> · ПРИНЯТА (ваша)\n\n"
        f"{_base_lines(req)}\n"
        f"📱 Телефон: {html.escape(req['phone'])}\n"
        f"👤 Клиент: {html.escape(req['full_name'] or '—')}"
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
    return text, None  # без кнопок


def card_canceled(req: dict):
    text = (
        f"❌ <b>Заявка №{req['id']}</b> · ОТМЕНЕНА клиентом\n\n"
        f"{_base_lines(req)}"
    )
    return text, None  # без кнопок


async def _broadcast_update(context: ContextTypes.DEFAULT_TYPE, req: dict) -> None:
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
            await context.bot.edit_message_text(
                chat_id=master_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            # Сообщение не изменилось/удалено/мастер заблокировал бота — пропускаем.
            logger.debug("Не удалось обновить сообщение мастера %s", master_id)


# ---------- Диалог с клиентом ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user = update.effective_user
    text = (
        f"👋 Привет, {html.escape(user.first_name)}!\n\n"
        "Это <b>Сантехник Рядом</b> — быстрое решение проблем с сантехникой.\n\n"
        "Протечка, засор, замена крана, установка техники — оставьте заявку, "
        "и мастер свяжется с вами в ближайшее время.\n\n"
        "Нажмите кнопку ниже, чтобы начать 👇"
    )
    await update.message.reply_html(text, reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "ℹ️ <b>Сантехник Рядом</b>\n\n"
        "/start — оставить заявку\n"
        "/cancel — отменить заполнение\n\n"
        "Опишите проблему, выберите район, укажите адрес, срочность и телефон."
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != str(config.ADMIN_CHAT_ID):
        return
    total, recent = database.get_stats()
    if total == 0:
        await update.message.reply_text("📊 Заявок пока нет.")
        return
    lines = [f"📊 <b>Всего заявок: {total}</b>", "", "Последние:"]
    for rid, created, problem, district, address, urgency, phone, status, taken_by in recent:
        mark = "🔴" if status == "taken" else "🟢"
        who = f" — {html.escape(taken_by)}" if taken_by else ""
        lines.append(
            f"\n#{rid} {mark}{who} · {created}\n"
            f"🛠 {html.escape(problem)}\n"
            f"📍 {html.escape(district)}, {html.escape(address or '—')}\n"
            f"⏱ {html.escape(urgency)} · 📱 {html.escape(phone)}"
        )
    await update.message.reply_html("\n".join(lines))


async def call_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        "🛠 Опишите, что случилось.\n\n"
        "Например: «Течёт труба под раковиной».\n\n"
        "Отменить — команда /cancel"
    )
    return PROBLEM


async def my_requests_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Клиент смотрит свои заявки и их статус, может удалить каждую."""
    query = update.callback_query
    await query.answer()
    rows = database.get_user_requests(query.from_user.id)
    if not rows:
        await query.message.reply_text(
            "У вас пока нет заявок. Нажмите /start, чтобы оставить первую 🚿"
        )
        return
    await query.message.reply_text("📋 Ваши заявки:")
    for r in rows:
        if r["status"] == "taken":
            status = "🔧 В работе — мастер скоро свяжется"
        else:
            status = "🟢 Открыта — ищем мастера"
        text = (
            f"<b>Заявка №{r['id']}</b> · {r['created_at'][:10]}\n"
            f"🛠 {html.escape(r['problem'])}\n"
            f"📍 {html.escape(r['district'])}, {html.escape(r['address'] or '—')}\n"
            f"{status}"
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑 Удалить заявку", callback_data=f"cancel_req:{r['id']}")]]
        )
        await query.message.reply_html(text, reply_markup=kb)


async def cancel_request_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Клиент удаляет (отменяет) свою заявку. Мастерам копии гасятся."""
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    ok = database.cancel_request(rid, query.from_user.id)
    if not ok:
        await query.answer("Эту заявку нельзя отменить.", show_alert=True)
        return
    await query.answer("Заявка удалена.")
    # Убираем кнопку и помечаем удаление в сообщении клиента
    try:
        await query.edit_message_text(
            f"❌ Заявка №{rid} удалена.", reply_markup=None
        )
    except Exception:
        pass
    # Гасим копии у мастеров
    req = database.get_request(rid)
    if req:
        await _broadcast_update(context, req)


async def get_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if len(text) < 3:
        await update.message.reply_text("Опишите проблему чуть подробнее 🙂")
        return PROBLEM
    context.user_data["problem"] = text
    await update.message.reply_text("📍 Выберите район города:", reply_markup=district_keyboard())
    return DISTRICT


async def get_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, value = query.data.split(":", 1)
    context.user_data["district"] = value
    await query.edit_message_text(f"📍 Район: {value}")
    await query.message.reply_text(
        "🏠 Напишите точный адрес: улица, дом, квартира.\n\n"
        "Например: «ул. Ленина, 12, кв. 5»."
    )
    return ADDRESS


async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if len(text) < 3:
        await update.message.reply_text("Укажите адрес чуть подробнее 🙂")
        return ADDRESS
    context.user_data["address"] = text
    await update.message.reply_text("⏱ Насколько срочно нужен мастер?", reply_markup=urgency_keyboard())
    return URGENCY


async def get_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, value = query.data.split(":", 1)
    context.user_data["urgency"] = value
    await query.edit_message_text(f"⏱ Срочность: {value}")
    await query.message.reply_text(
        "📱 Оставьте номер телефона для связи.\n\n"
        "Нажмите кнопку ниже или введите вручную.",
        reply_markup=phone_keyboard(),
    )
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
        if not is_valid_phone(phone):
            await update.message.reply_text(
                "🤔 Не похоже на номер. Введите в формате +7 999 123-45-67 "
                "или нажмите кнопку ниже.",
                reply_markup=phone_keyboard(),
            )
            return PHONE

    context.user_data["phone"] = phone
    data = context.user_data
    user = update.effective_user

    try:
        request_id = database.save_request(data, user)
    except Exception:
        logger.exception("Не удалось сохранить заявку")
        request_id = None

    number = f"№{request_id}" if request_id else ""

    confirmation = (
        f"✅ <b>Заявка {number} принята!</b>\n\n"
        f"🛠 Проблема: {html.escape(data['problem'])}\n"
        f"📍 Район: {html.escape(data['district'])}\n"
        f"🏠 Адрес: {html.escape(data['address'])}\n"
        f"⏱ Срочность: {html.escape(data['urgency'])}\n"
        f"📱 Телефон: {html.escape(data['phone'])}\n\n"
        "Мастер свяжется с вами в ближайшее время.\n"
        "Спасибо, что выбрали «Сантехник Рядом»! 💕💕"
    )
    await update.message.reply_html(confirmation, reply_markup=ReplyKeyboardRemove())

    username = f"@{user.username}" if user.username else "—"
    admin_text = (
        f"🆕 <b>Новая заявка {number}</b>\n\n"
        f"🛠 Проблема: {html.escape(data['problem'])}\n"
        f"📍 Район: {html.escape(data['district'])}\n"
        f"🏠 Адрес: {html.escape(data['address'])}\n"
        f"⏱ Срочность: {html.escape(data['urgency'])}\n"
        f"📱 Телефон: {html.escape(data['phone'])}\n\n"
        f"👤 Клиент: {html.escape(user.full_name)} ({username}, id {user.id})\n"
        f"🕒 {datetime.now():%d.%m.%Y %H:%M}"
    )
    try:
        await context.bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=admin_text, parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить заявку администратору")

    # Рассылка мастерам (телефон скрыт) + запоминаем id каждого сообщения
    if request_id:
        req = database.get_request(request_id)
        text, kb = card_free(req)
        for master_id in config.MASTER_IDS:
            try:
                msg = await context.bot.send_message(
                    chat_id=master_id, text=text, parse_mode="HTML", reply_markup=kb
                )
                database.save_master_message(request_id, int(master_id), msg.message_id)
            except Exception:
                logger.warning("Не удалось отправить заявку мастеру %s", master_id)

    context.user_data.clear()
    return ConversationHandler.END


# ---------- Кнопки мастеров ----------

async def take_request_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    master = query.from_user
    ok = database.take_request(rid, master.id, master_display_name(master))
    if not ok:
        await query.answer("Заявку уже взял другой мастер.", show_alert=True)
        req = database.get_request(rid)
        if req:
            await _broadcast_update(context, req)
        return
    await query.answer("Заявка ваша! Телефон клиента открыт.")
    req = database.get_request(rid)
    await _broadcast_update(context, req)


async def release_request_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    master = query.from_user
    ok = database.release_request(rid, master.id)
    if not ok:
        await query.answer("Передать может только тот, кто взял заявку.", show_alert=True)
        return
    await query.answer("Заявка передана. Снова доступна другим мастерам.")
    req = database.get_request(rid)
    await _broadcast_update(context, req)


# ---------- Прочее ----------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Заявка отменена. Чтобы начать заново — /start.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Ошибка:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Что-то пошло не так. Начните заново: /start"
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")


def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(call_master, pattern="^call_master$")],
        states={
            PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_problem)],
            DISTRICT: [CallbackQueryHandler(get_district, pattern="^district:")],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            URGENCY: [CallbackQueryHandler(get_urgency, pattern="^urgency:")],
            PHONE: [
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.CONTACT, get_phone
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )
