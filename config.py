"""Конфигурация приложения: загрузка переменных окружения из .env."""

import os

from dotenv import load_dotenv

load_dotenv()

# Клиентский бот (куда обращаются клиенты)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Мастерский бот (куда прилетают заявки мастерам)
MASTER_BOT_TOKEN = os.getenv("MASTER_BOT_TOKEN")

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Список id мастеров через запятую: MASTER_IDS=747862074,123456789
# Каждый мастер должен один раз нажать /start у МАСТЕРСКОГО бота.
_raw_masters = os.getenv("MASTER_IDS", "")
MASTER_IDS = [m.strip() for m in _raw_masters.split(",") if m.strip()]

# ===== ВКонтакте (мини-приложение) =====
# Защищённый ключ приложения VK Mini App — для проверки подписи параметров запуска.
VK_SECRET = os.getenv("VK_SECRET")
# VK ID мастеров (через запятую). Только они видят кабинет мастера.
_raw_vk_masters = os.getenv("VK_MASTER_IDS", "")
VK_MASTER_IDS = [m.strip() for m in _raw_vk_masters.split(",") if m.strip()]
# VK ID администраторов (через запятую). Видят переключатель Клиент/Мастер.
_raw_vk_admins = os.getenv("VK_ADMIN_IDS", "")
VK_ADMIN_IDS = [m.strip() for m in _raw_vk_admins.split(",") if m.strip()]
# Токен сообщества ВК — для отправки уведомлений в личку (messages.send).
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
# ID сообщества ВК.
VK_GROUP_ID = os.getenv("VK_GROUP_ID")


def validate() -> None:
    """Проверяем, что обязательные переменные заданы."""
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("MASTER_BOT_TOKEN", MASTER_BOT_TOKEN),
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
