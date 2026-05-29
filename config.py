"""Конфигурация приложения: загрузка переменных окружения из .env."""

import os

from dotenv import load_dotenv

# Локально читаем .env. На Render переменные приходят из окружения — load_dotenv
# просто ничего не найдёт и не помешает.
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Render автоматически задаёт PORT и RENDER_EXTERNAL_URL для web-сервисов.
# Если RENDER_EXTERNAL_URL есть — запускаемся в режиме webhook (для деплоя),
# иначе используем polling (удобно локально).
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")


def validate() -> None:
    """Проверяем, что обязательные переменные заданы. Иначе падаем с понятной ошибкой."""
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("ADMIN_CHAT_ID", ADMIN_CHAT_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Не заданы переменные окружения: "
            + ", ".join(missing)
            + ". Заполните файл .env (см. .env.example)."
        )
