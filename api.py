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

    # Запоминаем, кто взял заявку (до отмены), чтобы уведомить лично
    before = database.get_request(rid)
    taker_id = before["taken_by_id"] if before and before["status"] == "taken" else None

    ok = database.cancel_request(rid, user["id"])
    if not ok:
        return _cors(web.json_response({"error": "not_found"}, status=404))
    if _master_bot is not None:
        req = database.get_request(rid)
        if req:
            await requests_core.broadcast_update(_master_bot, req)  # уберёт пинг, если был
            # Если заявку уже взял мастер — личное уведомление ему
            if taker_id:
                await requests_core.notify_master_canceled(_master_bot, taker_id, req)
    return _cors(web.json_response({"ok": True}))


async def m_clear_history(request: web.Request) -> web.Response:
    """POST { initData } — прячет историю мастера (данные в базе остаются)."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    user = _master_auth(body.get("initData", ""))
    if not user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    n = database.clear_master_history(user["id"])
    # Удаляем из чата связанные уведомления «клиент отменил»
    if _master_bot is not None:
        for rid, message_id in database.get_cancel_notices_for_master(user["id"]):
            try:
                await _master_bot.delete_message(chat_id=user["id"], message_id=message_id)
            except Exception:
                pass
            database.delete_cancel_notice(rid, user["id"])
    return _cors(web.json_response({"ok": True, "cleared": n}))


async def create_request(request: web.Request) -> web.Response:
    """POST { initData, problem, district, address, urgency, phone } — создаёт заявку из мини-аппа клиента."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    user = _check_init_data(body.get("initData", ""))
    if not user or "id" not in user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))

    import re as _re
    problem = str(body.get("problem", "")).strip()
    district = str(body.get("district", "")).strip()
    address = str(body.get("address", "")).strip()
    urgency = str(body.get("urgency", "")).strip()
    phone = str(body.get("phone", "")).strip()

    # Валидация
    DISTRICTS = ["Индустриальный", "Северный", "Заягорбский", "Зашекснинский", "Пригород"]
    URGENCIES = ["Срочно — авария", "Сегодня", "В ближайшие дни"]
    digits = _re.sub(r"\D", "", phone)
    if (len(problem) < 3 or district not in DISTRICTS or len(address) < 3
            or urgency not in URGENCIES or not (10 <= len(digits) <= 15)):
        return _cors(web.json_response({"error": "invalid"}, status=400))

    # Объект пользователя для save_request
    class _U:
        id = user["id"]
        username = user.get("username")
        full_name = (user.get("first_name", "") + " " + user.get("last_name", "")).strip() or None

    data = {"problem": problem, "district": district, "address": address,
            "urgency": urgency, "phone": phone}
    request_id = database.save_request(data, _U())

    # Рассылаем мастерам через мастерский бот
    if _master_bot is not None:
        try:
            await requests_core.broadcast_new_request(_master_bot, request_id)
        except Exception:
            logger.warning("Не удалось разослать заявку мастерам")

    return _cors(web.json_response({"ok": True, "id": request_id}))


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/my_requests", my_requests)
    app.router.add_post("/api/edit", edit_request)
    app.router.add_post("/api/delete", delete_request)
    app.router.add_post("/api/create", create_request)
    app.router.add_get("/api/m/board", m_board)
    app.router.add_get("/api/m/mine", m_mine)
    app.router.add_post("/api/m/action", m_action)
    app.router.add_post("/api/m/clear_history", m_clear_history)
    for path in ("/api/my_requests", "/api/edit", "/api/delete", "/api/create", "/api/m/board", "/api/m/mine", "/api/m/action", "/api/m/clear_history"):
        app.router.add_options(path, handle_options)
    return app


async def start_api(host: str = "127.0.0.1", port: int = 8081) -> web.AppRunner:
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API мини-аппа запущен на %s:%s", host, port)
    return runner


# ----------------- API для мастерского мини-аппа -----------------
# Подпись мастерского мини-аппа проверяется токеном МАСТЕРСКОГО бота.

def _check_master_init(init_data: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", config.MASTER_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    try:
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def _master_auth(init_data: str) -> dict | None:
    """Проверяет подпись и что пользователь — мастер из списка."""
    user = _check_master_init(init_data)
    if not user or "id" not in user:
        return None
    if str(user["id"]) not in [str(m) for m in config.MASTER_IDS]:
        return None
    return user


def _master_card_dict(r: dict, show_contact: bool) -> dict:
    """Заявка для мастера. Контакты только если show_contact=True (его заявка)."""
    d = {
        "id": r["id"],
        "problem": r["problem"],
        "district": r["district"],
        "address": r["address"] or "",
        "urgency": r["urgency"],
        "status": r["status"],
        "date": (r["created_at"] or "")[:10],
    }
    if show_contact:
        d["phone"] = r.get("phone", "")
        d["username"] = r.get("username") or ""
        d["full_name"] = r.get("full_name") or ""
    return d


async def m_board(request: web.Request) -> web.Response:
    """Доска: открытые заявки (без контактов)."""
    if not _master_auth(request.query.get("initData", "")):
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rows = database.get_open_requests()
    return _cors(web.json_response({"requests": [_master_card_dict(r, False) for r in rows]}))


async def m_mine(request: web.Request) -> web.Response:
    """Мои заявки: в работе (с контактами) + история."""
    user = _master_auth(request.query.get("initData", ""))
    if not user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    active = database.get_master_active(user["id"])
    history = database.get_master_history(user["id"])
    return _cors(web.json_response({
        "active": [_master_card_dict(r, True) for r in active],
        "history": [_master_card_dict(r, True) for r in history],
    }))


async def m_action(request: web.Request) -> web.Response:
    """POST { initData, id, action: take|release|complete }."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    user = _master_auth(body.get("initData", ""))
    if not user:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rid = body.get("id")
    action = body.get("action")
    if not isinstance(rid, int) or action not in ("take", "release", "complete"):
        return _cors(web.json_response({"error": "bad_request"}, status=400))

    mid = user["id"]
    mname = ("@" + user["username"]) if user.get("username") else (user.get("first_name") or f"id {mid}")

    if action == "take":
        ok = database.take_request(rid, mid, mname)
        if ok and _client_bot is not None:
            req = database.get_request(rid)
            if req:
                await requests_core.notify_client(_client_bot, req, "taken")
    elif action == "release":
        ok = database.release_request(rid, mid)
        if ok and _client_bot is not None:
            req = database.get_request(rid)
            if req:
                await requests_core.notify_client(_client_bot, req, "released")
    else:  # complete
        ok = database.complete_request(rid, mid)
        if ok and _client_bot is not None:
            req = database.get_request(rid)
            if req:
                await requests_core.notify_client(_client_bot, req, "done")

    if not ok:
        return _cors(web.json_response({"error": "not_allowed"}, status=403))
    # Обновим карточку в чате мастеров (бот) тоже
    if _master_bot is not None:
        req = database.get_request(rid)
        if req:
            await requests_core.broadcast_update(_master_bot, req)
    return _cors(web.json_response({"ok": True}))
