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

# Связка мастеров ТГ↔ВК: один человек с двумя ID.
# Формат: MASTER_LINKS=ТГ_ID:ВК_ID,ТГ_ID2:ВК_ID2
# Пример: MASTER_LINKS=747862074:1115336850
_raw_links = os.getenv("MASTER_LINKS", "")
MASTER_LINKS = []  # список пар (tg_id, vk_id) как строки
for _pair in _raw_links.split(","):
    _pair = _pair.strip()
    if ":" in _pair:
        _tg, _vk = _pair.split(":", 1)
        _tg, _vk = _tg.strip(), _vk.strip()
        if _tg and _vk:
            MASTER_LINKS.append((_tg, _vk))


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


def linked_ids(any_id) -> list[str]:
    """Возвращает все ID (ТГ и ВК) одного мастера по любому из его ID.
    Если связки нет — вернёт список из одного этого ID."""
    sid = str(any_id)
    result = {sid}
    for tg, vk in MASTER_LINKS:
        if sid == tg or sid == vk:
            result.add(tg)
            result.add(vk)
    return list(result)


def tg_id_for(any_id):
    """ТГ ID мастера по любому его ID (или None)."""
    sid = str(any_id)
    for tg, vk in MASTER_LINKS:
        if sid == tg or sid == vk:
            return tg
    # если сам является ТГ-мастером
    if sid in [str(m) for m in MASTER_IDS]:
        return sid
    return None


def vk_id_for(any_id):
    """ВК ID мастера по любому его ID (или None)."""
    sid = str(any_id)
    for tg, vk in MASTER_LINKS:
        if sid == tg or sid == vk:
            return vk
    if sid in [str(m) for m in VK_MASTER_IDS]:
        return sid
    return None
