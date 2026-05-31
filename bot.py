"""Точка входа: запуск Telegram-бота «Сантехник Рядом»."""

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import config
import database
from handlers import (
    build_conversation_handler,
    error_handler,
    help_command,
    cancel_request_cb,
    my_requests_cb,
    release_request_cb,
    start,
    stats_command,
    take_request_cb,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def ensure_event_loop() -> None:
    """На Python 3.14 asyncio.get_event_loop() не создаёт цикл сам — создаём вручную."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def main() -> None:
    config.validate()
    ensure_event_loop()
    database.init_db()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(build_conversation_handler())
    app.add_handler(CallbackQueryHandler(my_requests_cb, pattern="^my_requests$"))
    app.add_handler(CallbackQueryHandler(cancel_request_cb, pattern="^cancel_req:"))
    # Кнопки мастеров (вне диалога с клиентом)
    app.add_handler(CallbackQueryHandler(take_request_cb, pattern="^take:"))
    app.add_handler(CallbackQueryHandler(release_request_cb, pattern="^release:"))
    app.add_error_handler(error_handler)

    if config.WEBHOOK_URL:
        logger.info("Запуск в режиме webhook на %s", config.WEBHOOK_URL)
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path=config.TELEGRAM_BOT_TOKEN,
            webhook_url=f"{config.WEBHOOK_URL}/{config.TELEGRAM_BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Запуск в режиме polling")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
