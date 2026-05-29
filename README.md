# 🚰 Сантехник Рядом

Telegram-бот для быстрого вызова сантехника. Клиент описывает проблему, указывает район, срочность и телефон — заявка приходит администратору в Telegram.

## Возможности

- `/start` — приветствие + кнопка **🚰 Вызвать мастера**
- Пошаговый сбор заявки (проблема → район → срочность → телефон) через `ConversationHandler`
- Inline-кнопки, кнопка «поделиться контактом», `/cancel` для отмены
- Подтверждение клиенту + отправка заявки администратору
- Логирование и обработка ошибок
- Локально работает через **polling**, на Render — автоматически через **webhook**

## Структура проекта

```
santehnik-ryadom/
├── bot.py            # точка входа (polling / webhook)
├── config.py         # переменные окружения
├── handlers.py       # логика диалога (ConversationHandler)
├── keyboards.py      # клавиатуры
├── requirements.txt
├── .env.example
├── render.yaml       # конфиг деплоя на Render
└── .gitignore
```

## Подготовка

1. Создайте бота у [@BotFather](https://t.me/BotFather) → получите `TELEGRAM_BOT_TOKEN`.
2. Узнайте свой `chat_id` у [@userinfobot](https://t.me/userinfobot) — это `ADMIN_CHAT_ID` (куда придут заявки).

## Запуск локально

```bash
# 1. Виртуальное окружение
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Зависимости
pip install -r requirements.txt

# 3. Переменные окружения
cp .env.example .env               # Windows: copy .env.example .env
# затем впишите свои TELEGRAM_BOT_TOKEN и ADMIN_CHAT_ID в .env

# 4. Запуск (режим polling)
python bot.py
```

Откройте бота в Telegram и отправьте `/start`.

## Деплой на Render

Бот деплоится как **Web Service** (free-план): при наличии переменной `RENDER_EXTERNAL_URL`
он сам переключается в режим webhook и слушает порт из `PORT` — оба значения Render задаёт автоматически.

**Шаги:**

1. Запушьте проект на GitHub (см. ниже).
2. На [render.com](https://render.com) → **New** → **Web Service** → подключите репозиторий.
3. Render подхватит `render.yaml`. Если настраиваете вручную:
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
4. В разделе **Environment** добавьте переменные:
   - `TELEGRAM_BOT_TOKEN`
   - `ADMIN_CHAT_ID`
5. **Create Web Service** — после сборки бот заработает.

> ⚠️ **Про free-план:** бесплатный web-сервис «засыпает» после 15 минут без активности.
> Первое сообщение после простоя разбудит сервис (Telegram повторит доставку webhook),
> но с задержкой ~30–60 секунд. Чтобы бот не засыпал, используйте платный план
> или тип **Background Worker** (от $7/мес) со `startCommand: python bot.py` — он работает через polling без webhook.

## Git: инициализация и пуш на GitHub

Создайте пустой репозиторий на GitHub (без README), затем:

```bash
git init
git add .
git commit -m "MVP: Telegram-бот «Сантехник Рядом»"
git branch -M main
git remote add origin https://github.com/USERNAME/santehnik-ryadom.git
git push -u origin main
```

Замените `USERNAME` на ваш логин. Файл `.env` не попадёт в репозиторий — он в `.gitignore`.

## Команды бота

| Команда   | Действие                          |
|-----------|-----------------------------------|
| `/start`  | Приветствие и кнопка вызова мастера |
| `/cancel` | Отменить текущую заявку            |
