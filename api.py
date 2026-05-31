"""HTTP API для мини-аппа клиента: список, детали, редактирование, удаление заявок.
Все запросы проверяют подпись Telegram (initData) — чужие заявки недоступны."""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from aiohttp import web

import config
import database
import requests_core

logger = logging.getLogger(__name__)

# Ссылки на ботов проставляются при запуске (для обновления карточек/уведомлений)
_master_bot = None
_client_bot = None


def set_bots(master_bot, client_bot) -> None:
    global _master_bot, _client_bot
    _master_bot = master_bot
    _client_bot = client_bot


def _check_init_data(init_data: str) -> dict | None:
    """Проверяет подпись Telegram WebApp initData. Возвращает user dict или None."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    try:
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def _auth(request: web.Request) -> dict | None:
    """Достаёт и проверяет initData из query или JSON-тела."""
    init_data = request.get("_init_data")
    return init_data


def _cors(resp: web.Response) -> web.Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


async def handle_options(request: web.Request) -> web.Response:
    return _cors(web.Response())


def _req_to_dict(r: dict) -> dict:
    return {
        "id": r["id"],
        "problem": r["problem"],
        "district": r["district"],
        "address": r["address"] or "",
        "urgency": r["urgency"],
        "phone": r["phone"],
        "status": r["status"],
        "date": (r["created_at"] or "")[:10],
    }


async def my_requests(request: web.Request) -> web.Response:
    user = _check_init_data(request.query.get("initData", ""))
    if not user or "id" not in user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rows = database.get_user_requests(user["id"])
    return _cors(web.json_response({"requests": [_req_to_dict(r) for r in rows]}))


async def edit_request(request: web.Request) -> web.Response:
    """POST: { initData, id, fields:{...} } — редактирует заявку клиента."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    user = _check_init_data(body.get("initData", ""))
    if not user or "id" not in user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))

    rid = body.get("id")
    fields = body.get("fields", {})
    if not isinstance(rid, int) or not isinstance(fields, dict):
        return _cors(web.json_response({"error": "bad_request"}, status=400))

    status = database.update_request(rid, user["id"], fields)
    if status is None:
        return _cors(web.json_response({"error": "not_editable"}, status=403))

    # Если заявку уже взяли — обновим карточку у мастеров (адрес/телефон могли измениться)
    if status == "taken" and _master_bot is not None:
        req = database.get_request(rid)
        if req:
            await requests_core.broadcast_update(_master_bot, req)

    req = database.get_request(rid)
    return _cors(web.json_response({"ok": True, "request": _req_to_dict(req)}))


async def delete_request(request: web.Request) -> web.Response:
    """POST: { initData, id } — удаляет (отменяет) заявку клиента."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    user = _check_init_data(body.get("initData", ""))
    if not user or "id" not in user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rid = body.get("id")
    if not isinstance(rid, int):
        return _cors(web.json_response({"error": "bad_request"}, status=400))

    ok = database.cancel_request(rid, user["id"])
    if not ok:
        return _cors(web.json_response({"error": "not_found"}, status=404))
    # Гасим карточки у мастеров
    if _master_bot is not None:
        req = database.get_request(rid)
        if req:
            await requests_core.broadcast_update(_master_bot, req)
    return _cors(web.json_response({"ok": True}))


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/my_requests", my_requests)
    app.router.add_post("/api/edit", edit_request)
    app.router.add_post("/api/delete", delete_request)
    for path in ("/api/my_requests", "/api/edit", "/api/delete"):
        app.router.add_options(path, handle_options)
    return app


async def start_api(host: str = "127.0.0.1", port: int = 8081) -> web.AppRunner:
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API мини-аппа запущен на %s:%s", host, port)
    return runner
