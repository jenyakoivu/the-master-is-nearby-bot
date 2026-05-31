"""Мастерский бот: мастер видит заявки, берёт и передаёт их."""

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

import config
import database
import requests_core

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if str(user.id) in [str(m) for m in config.MASTER_IDS]:
        await update.message.reply_html(
            "🧰 <b>Сантехник Рядом — кабинет мастера</b>\n\n"
            "Вы в системе. Новые заявки будут приходить сюда автоматически.\n"
            "Берите свободные заявки кнопкой «✋ Взять заявку»."
        )
    else:
        await update.message.reply_html(
            "Этот бот — только для мастеров сервиса «Сантехник Рядом».\n\n"
            f"Ваш id: <code>{user.id}</code>\n"
            "Передайте его администратору, чтобы он добавил вас в список мастеров."
        )


async def take_request_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rid = int(query.data.split(":", 1)[1])
    master = query.from_user
    ok = database.take_request(rid, master.id, requests_core.master_display_name(master))
    if not ok:
        await query.answer("Заявку уже взял другой мастер.", show_alert=True)
        req = database.get_request(rid)
        if req:
            await requests_core.broadcast_update(context.bot, req)
        return
    await query.answer("Заявка ваша! Телефон клиента открыт.")
    req = database.get_request(rid)
    await requests_core.broadcast_update(context.bot, req)


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
    await requests_core.broadcast_update(context.bot, req)


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(take_request_cb, pattern="^take:"))
    app.add_handler(CallbackQueryHandler(release_request_cb, pattern="^release:"))
