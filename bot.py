"""Точка входа: запускает клиентский и мастерский боты в одном процессе.

Оба бота крутятся в одном event loop. Клиентский бот при новой заявке
мгновенно рассылает её мастерам через объект мастерского бота.
"""

import asyncio
import logging

from telegram.ext import Application

import client_bot
import config
import database
import master_bot
from api import start_api

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def run() -> None:
    config.validate()
    database.init_db()

    client_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    master_app = Application.builder().token(config.MASTER_BOT_TOKEN).build()

    # Клиентский бот пишет мастерам через мастерского, мастерский уведомляет
    # клиентов через клиентского — передаём ссылки в обе стороны.
    client_app.bot_data["master_bot"] = master_app.bot
    master_app.bot_data["client_bot"] = client_app.bot

    client_bot.register(client_app)
    master_bot.register(master_app)

    # Ручной жизненный цикл: run_polling() блокировал бы цикл и не дал бы
    # запустить второй бот. Поэтому запускаем оба вручную в общем loop.
    await client_app.initialize()
    await master_app.initialize()
    await client_app.start()
    await master_app.start()
    await client_app.updater.start_polling(drop_pending_updates=True)
    await master_app.updater.start_polling(drop_pending_updates=True)

    # API для мини-аппа (на localhost, наружу его пускает Caddy по https)
    from api import set_bots
    set_bots(master_app.bot, client_app.bot)
    api_runner = await start_api()

    # Фоновый слушатель событий ВК (нажатия кнопок в уведомлениях)
    import vk_longpoll
    vk_lp_task = asyncio.create_task(vk_longpoll.run_longpoll())

    logger.info("Оба бота запущены: клиентский и мастерский")

    # Держим процесс живым, пока не остановят.
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        vk_lp_task.cancel()
        await api_runner.cleanup()
        await client_app.updater.stop()
        await master_app.updater.stop()
        await client_app.stop()
        await master_app.stop()
        await client_app.shutdown()
        await master_app.shutdown()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка ботов")


if __name__ == "__main__":
    main()
