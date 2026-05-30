"""Логика диалога: приём заявки, сохранение в базу и отправка администратору."""

import html
import logging
import re
from datetime import datetime

from telegram import ReplyKeyboardRemove, Update
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

# Состояния диалога
PROBLEM, DISTRICT, ADDRESS, URGENCY, PHONE = range(5)


def is_valid_phone(text: str) -> bool:
    """Простая проверка телефона: 10–15 цифр (с учётом +, пробелов, скобок, дефисов)."""
    digits = re.sub(r"\D", "", text)
    return 10 <= len(digits) <= 15


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start — приветствие и кнопка вызова мастера."""
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
    """Команда /help — краткая справка."""
    await update.message.reply_html(
        "ℹ️ <b>Сантехник Рядом</b>\n\n"
        "/start — оставить заявку на вызов мастера\n"
        "/cancel — отменить заполнение заявки\n\n"
        "Опишите проблему, выберите район, укажите адрес, срочность и телефон — "
        "и мы свяжемся с вами."
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /stats — статистика заявок. Доступна только администратору."""
    if str(update.effective_user.id) != str(config.ADMIN_CHAT_ID):
        return  # для остальных команда «не существует»
    total, recent = database.get_stats()
    if total == 0:
        await update.message.reply_text("📊 Заявок пока нет.")
        return
    lines = [f"📊 <b>Всего заявок: {total}</b>", "", "Последние:"]
    for rid, created, problem, district, address, urgency, phone in recent:
        lines.append(
            f"\n#{rid} · {created}\n"
            f"🛠 {html.escape(problem)}\n"
            f"📍 {html.escape(district)}, {html.escape(address or '—')}\n"
            f"⏱ {html.escape(urgency)} · 📱 {html.escape(phone)}"
        )
    await update.message.reply_html("\n".join(lines))


async def call_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Нажата кнопка «Вызвать мастера» — начинаем сбор данных."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        "🛠 Опишите, что случилось.\n\n"
        "Например: «Течёт труба под раковиной» или «Засорился унитаз».\n\n"
        "Отменить в любой момент — команда /cancel"
    )
    return PROBLEM


async def get_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["problem"] = update.message.text.strip()
    await update.message.reply_text(
        "📍 Выберите район города:",
        reply_markup=district_keyboard(),
    )
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
    context.user_data["address"] = update.message.text.strip()
    await update.message.reply_text(
        "⏱ Насколько срочно нужен мастер?",
        reply_markup=urgency_keyboard(),
    )
    return URGENCY


async def get_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, value = query.data.split(":", 1)
    context.user_data["urgency"] = value
    await query.edit_message_text(f"⏱ Срочность: {value}")
    await query.message.reply_text(
        "📱 Оставьте номер телефона для связи.\n\n"
        "Можно нажать кнопку ниже или ввести номер вручную.",
        reply_markup=phone_keyboard(),
    )
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финальный шаг: проверяем телефон, сохраняем заявку, уведомляем клиента и админа."""
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
        if not is_valid_phone(phone):
            await update.message.reply_text(
                "🤔 Не похоже на номер телефона. "
                "Введите его в формате +7 999 123-45-67 или нажмите кнопку ниже.",
                reply_markup=phone_keyboard(),
            )
            return PHONE  # остаёмся на этом же шаге

    context.user_data["phone"] = phone
    data = context.user_data
    user = update.effective_user

    # Сохраняем заявку в базу
    try:
        request_id = database.save_request(data, user)
    except Exception:
        logger.exception("Не удалось сохранить заявку в базу")
        request_id = None

    number = f"№{request_id}" if request_id else ""

    # Подтверждение клиенту
    confirmation = (
        f"✅ <b>Заявка {number} принята!</b>\n\n"
        f"🛠 Проблема: {html.escape(data['problem'])}\n"
        f"📍 Район: {html.escape(data['district'])}\n"
        f"🏠 Адрес: {html.escape(data['address'])}\n"
        f"⏱ Срочность: {html.escape(data['urgency'])}\n"
        f"📱 Телефон: {html.escape(data['phone'])}\n\n"
        "Мастер свяжется с вами в ближайшее время.\n"
        "Спасибо, что выбрали «Сантехник Рядом»! 🚿"
    )
    await update.message.reply_html(confirmation, reply_markup=ReplyKeyboardRemove())

    # Уведомление администратору
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
        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=admin_text,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Не удалось отправить заявку администратору")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel — отмена текущей заявки."""
    context.user_data.clear()
    await update.message.reply_text(
        "Заявка отменена. Чтобы начать заново, отправьте /start.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок: логируем и мягко уведомляем пользователя."""
    logger.error("Ошибка при обработке обновления:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз или начните заново: /start"
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке пользователю")


def build_conversation_handler() -> ConversationHandler:
    """Собираем ConversationHandler для приёма заявки."""
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
