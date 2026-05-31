"""Клавиатуры бота: inline-кнопки и кнопка отправки контакта."""

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Районы Череповца (+ пригород отдельно)
DISTRICTS = [
    "Индустриальный",
    "Северный",
    "Заягорбский",
    "Зашекснинский",
    "Пригород",
]


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню с кнопками вызова мастера и просмотра заявок."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👷 Вызвать мастера", callback_data="call_master")],
            [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
        ]
    )


def district_keyboard() -> InlineKeyboardMarkup:
    """Выбор района города."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(name, callback_data=f"district:{name}")] for name in DISTRICTS]
    )


def urgency_keyboard() -> InlineKeyboardMarkup:
    """Выбор срочности заявки."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔥 Срочно — авария", callback_data="urgency:Срочно — авария")],
            [InlineKeyboardButton("📅 Сегодня", callback_data="urgency:Сегодня")],
            [InlineKeyboardButton("🗓 В ближайшие дни", callback_data="urgency:В ближайшие дни")],
        ]
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка «поделиться контактом» + возможность ввести номер вручную."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Или введите номер вручную",
    )
