"""Логика диалога: приём заявки и отправка её администратору."""

import html
import logging
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
from keyboards import main_menu_keyboard, phone_keyboard, urgency_keyboard

logger = logging.getLogger(__name__)

# Состояния диалога
PROBLEM, DISTRICT, URGENCY, PHONE = range(4)


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


async def call_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Нажата кнопка «Вызвать мастера» — начинаем сбор данных."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        "🛠 Опишите, что случилось.\n\n"
        "Например: «Течёт труба под раковиной» или «Засорился унитаз»."
    )
    return PROBLEM


async def get_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["problem"] = update.message.text.strip()
    await update.message.reply_text("📍 В каком районе / по какому адресу вы находитесь?")
    return DISTRICT


async def get_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["district"] = update.message.text.strip()
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
    """Финальный шаг: сохраняем телефон, подтверждаем и отправляем заявку админу."""
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    context.user_data["phone"] = phone

    data = context.user_data
    user = update.effective_user

    # Подтверждение пользователю
    confirmation = (
        "✅ <b>Заявка принята!</b>\n\n"
        f"🛠 Проблема: {html.escape(data['problem'])}\n"
        f"📍 Район: {html.escape(data['district'])}\n"
        f"⏱ Срочность: {html.escape(data['urgency'])}\n"
        f"📱 Телефон: {html.escape(data['phone'])}\n\n"
        "Мастер свяжется с вами в ближайшее время.\n"
        "Спасибо, что выбрали «Сантехник Рядом»! 🚿"
    )
    await update.message.reply_html(confirmation, reply_markup=ReplyKeyboardRemove())

    # Уведомление администратору
    username = f"@{user.username}" if user.username else "—"
    admin_text = (
        "🆕 <b>Новая заявка «Сантехник Рядом»</b>\n\n"
        f"🛠 Проблема: {html.escape(data['problem'])}\n"
        f"📍 Район: {html.escape(data['district'])}\n"
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
        # Заявку клиенту уже подтвердили — просто логируем сбой доставки админу.
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
            DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_district)],
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
