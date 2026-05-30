"""Точка входа: запуск Telegram-бота «Сантехник Рядом».

Локально и на сервере работает через polling.
На Render (есть RENDER_EXTERNAL_URL) автоматически переключается на webhook.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler

import config
import database
from handlers import (
    build_conversation_handler,
    error_handler,
    help_command,
    start,
    stats_command,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Не засоряем логи внутренними запросами библиотеки.
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
    app.add_error_handler(error_handler)

    if config.WEBHOOK_URL:
        # Режим webhook — для деплоя на Render (web service).
        logger.info("Запуск в режиме webhook на %s", config.WEBHOOK_URL)
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path=config.TELEGRAM_BOT_TOKEN,
            webhook_url=f"{config.WEBHOOK_URL}/{config.TELEGRAM_BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        # Режим polling — для локальной разработки и обычного сервера.
        logger.info("Запуск в режиме polling")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
