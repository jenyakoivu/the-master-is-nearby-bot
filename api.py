"""Маленький HTTP API для мини-аппа.
Отдаёт заявки конкретного клиента. Проверяет подпись Telegram (initData),
чтобы нельзя было запросить чужие заявки."""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from aiohttp import web

import config
import database

logger = logging.getLogger(__name__)


def _check_init_data(init_data: str) -> dict | None:
    """Проверяет подпись Telegram WebApp initData.
    Возвращает данные пользователя (dict) если подпись верна, иначе None."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    # Строка для проверки: все поля кроме hash, отсортированы, через \n
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    # Секретный ключ = HMAC-SHA256 от токена бота с ключом "WebAppData"
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    try:
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


# CORS — чтобы страница с github.io могла обращаться к API
def _cors(resp: web.Response) -> web.Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


async def handle_options(request: web.Request) -> web.Response:
    return _cors(web.Response())


async def my_requests(request: web.Request) -> web.Response:
    """GET-параметр initData (или тело) -> заявки этого клиента."""
    init_data = request.query.get("initData", "")
    user = _check_init_data(init_data)
    if not user or "id" not in user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))

    rows = database.get_user_requests(user["id"])
    # отдаём только нужные поля
    items = [
        {
            "id": r["id"],
            "problem": r["problem"],
            "district": r["district"],
            "address": r["address"] or "",
            "urgency": r["urgency"],
            "status": r["status"],
            "date": (r["created_at"] or "")[:10],
        }
        for r in rows
    ]
    return _cors(web.json_response({"requests": items}))


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/my_requests", my_requests)
    app.router.add_options("/api/my_requests", handle_options)
    return app


async def start_api(host: str = "127.0.0.1", port: int = 8081) -> web.AppRunner:
    """Запускает API внутри текущего event loop (рядом с ботами)."""
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API мини-аппа запущен на %s:%s", host, port)
    return runner
