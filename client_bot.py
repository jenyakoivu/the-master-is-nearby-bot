"""Клиентский бот — только интерфейс статусов.
Создание/просмотр/редактирование заявок — в мини-аппе. В чате: приветствие
с кнопкой мини-аппа и сообщения-статусы по заявкам (одна заявка = одно сообщение)."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

import database
from keyboards import MINIAPP_URL

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"👋 Здравствуйте, {user.first_name}!\n\n"
        "Это <b>Мастер Рядом</b> — вызов мастера в Череповце за пару минут.\n\n"
        "Опишите проблему в приложении — мастер свяжется с вами. "
        "Здесь, в чате, будут приходить статусы ваших заявок."
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Оставить заявку", web_app=WebAppInfo(url=MINIAPP_URL))]]
    )
    await update.message.reply_html(text, reply_markup=kb)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Открыть приложение", web_app=WebAppInfo(url=MINIAPP_URL))]]
    )
    await update.message.reply_html(
        "ℹ️ <b>Мастер Рядом</b>\n\n"
        "Все заявки — в приложении: кнопка ниже или «Мои заявки» рядом с полем ввода.\n"
        "В чат приходят статусы ваших заявок.",
        reply_markup=kb,
    )


async def cstatus_del_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Удалить» под выполненной заявкой — убирает сообщение из чата."""
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    await query.answer("Убрано.")
    try:
        await query.message.delete()
    except Exception:
        pass
    database.delete_client_status_message(rid)


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(cstatus_del_cb, pattern="^cstatus_del:"))
