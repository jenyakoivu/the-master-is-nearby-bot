"""Клиентский бот: оставить заявку, мои заявки, отмена.
Заявки рассылаются мастерам через мастерский бот (берётся из bot_data['master_bot'])."""

import html
import logging
import re

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

import database
import requests_core
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


async def my_requests_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        status = "🔧 В работе — мастер скоро свяжется" if r["status"] == "taken" else "🟢 Открыта — ищем мастера"
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
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    before = database.get_request(rid)
    taker_id = before["taken_by_id"] if before and before["status"] == "taken" else None
    ok = database.cancel_request(rid, query.from_user.id)
    if not ok:
        await query.answer("Эту заявку нельзя отменить.", show_alert=True)
        return
    await query.answer("Заявка удалена.")
    try:
        await query.edit_message_text(f"❌ Заявка №{rid} удалена.", reply_markup=None)
    except Exception:
        pass
    # Убираем пинг у мастеров; если заявку взяли — уведомляем взявшего лично
    req = database.get_request(rid)
    master_bot = context.bot_data.get("master_bot")
    if req and master_bot:
        await requests_core.broadcast_update(master_bot, req)
        if taker_id:
            await requests_core.notify_master_canceled(master_bot, taker_id, req)


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
        "Спасибо, что выбрали «Сантехник Рядом»! 💖"
    )
    await update.message.reply_html(confirmation, reply_markup=ReplyKeyboardRemove())

    # Мгновенно рассылаем заявку мастерам через мастерский бот
    if request_id:
        master_bot = context.bot_data.get("master_bot")
        if master_bot:
            try:
                await requests_core.broadcast_new_request(master_bot, request_id)
            except Exception:
                logger.exception("Не удалось разослать заявку мастерам")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Заявка отменена. Чтобы начать заново — /start.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(call_master, pattern="^call_master$")],
        states={
            PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_problem)],
            DISTRICT: [CallbackQueryHandler(get_district, pattern="^district:")],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            URGENCY: [CallbackQueryHandler(get_urgency, pattern="^urgency:")],
            PHONE: [MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.CONTACT, get_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(build_conversation_handler())
    app.add_handler(CallbackQueryHandler(my_requests_cb, pattern="^my_requests$"))
    app.add_handler(CallbackQueryHandler(cancel_request_cb, pattern="^cancel_req:"))
