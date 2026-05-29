"""Клавиатуры бота: inline-кнопки и кнопка отправки контакта."""

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню с кнопкой вызова мастера."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚰 Вызвать мастера", callback_data="call_master")]]
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
