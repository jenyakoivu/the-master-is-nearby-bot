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
import vk_notify

logger = logging.getLogger(__name__)

# Ссылки на ботов проставляются при запуске (для обновления карточек/уведомлений)
_master_bot = None
_client_bot = None


def set_bots(master_bot, client_bot) -> None:
    global _master_bot, _client_bot
    _master_bot = master_bot
    _client_bot = client_bot


async def sync_new(request_id: int) -> None:
    """Новая заявка из любого канала: пинги мастерам в ОБА канала (ТГ + ВК),
    статус — клиенту в его канал."""
    req = database.get_request(request_id)
    if not req:
        return
    # Мастерам — пинги в оба канала (заявка общая)
    if _master_bot is not None:
        try:
            await requests_core.broadcast_new_request(_master_bot, request_id)
        except Exception:
            logger.warning("ТГ-пинг не отправлен")
    try:
        vk_notify.send_master_pings(request_id)
    except Exception:
        logger.warning("ВК-пинг не отправлен")
    # Клиенту — статус в его канал
    await _sync_client(req)


async def sync_update(req: dict) -> None:
    """Изменение статуса из любого канала: обновить пинги мастеров в ОБОИХ каналах
    и статус клиента в его канале."""
    if not req:
        return
    # Мастерам: ТГ-пинги
    if _master_bot is not None:
        try:
            await requests_core.broadcast_update(_master_bot, req)
        except Exception:
            logger.warning("ТГ-обновление не отправлено")
    # Мастерам: ВК-пинги (снова свободна → пинг заново, иначе убрать)
    try:
        if req["status"] == "new":
            vk_notify.remove_master_pings(req["id"])
            vk_notify.send_master_pings(req["id"])
        else:
            vk_notify.remove_master_pings(req["id"])
    except Exception:
        logger.warning("ВК-обновление мастеров не отправлено")
    # Клиенту — статус в его канал
    await _sync_client(req)


async def _sync_client(req: dict) -> None:
    """Шлёт статус клиенту в ТОТ канал, откуда заявка (tg → телеграм, vk → ВК)."""
    source = req.get("source") or "tg"
    if source == "vk":
        try:
            vk_notify.refresh_client_status(req)
        except Exception:
            logger.warning("ВК-статус клиента не отправлен")
    else:
        if _client_bot is not None:
            try:
                await requests_core.refresh_client_status(_client_bot, req)
            except Exception:
                logger.warning("ТГ-статус клиента не отправлен")


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
        "phone_show": requests_core.format_ru_phone(r["phone"]),
        "status": r["status"],
        "released_once": r["released_once"] if "released_once" in r.keys() else 0,
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

    req = database.get_request(rid)
    if req:
        await sync_update(req)
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
    req = database.get_request(rid)
    if req:
        await sync_update(req)  # уберёт пинги в обоих каналах + статус клиенту
        # личное уведомление взявшему мастеру (ТГ)
        if taker_id and _master_bot is not None:
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

    problem = str(body.get("problem", "")).strip()
    district = str(body.get("district", "")).strip()
    address = str(body.get("address", "")).strip()
    urgency = str(body.get("urgency", "")).strip()
    phone_raw = str(body.get("phone", "")).strip()

    # Валидация. Телефон — строго РФ (нормализуем к +7XXXXXXXXXX).
    DISTRICTS = ["Индустриальный", "Северный", "Заягорбский", "Зашекснинский", "Пригород"]
    URGENCIES = ["Срочно — авария", "Сегодня", "В ближайшие дни"]
    phone = requests_core.normalize_ru_phone(phone_raw)
    if (len(problem) < 3 or district not in DISTRICTS or len(address) < 3
            or urgency not in URGENCIES or phone is None):
        return _cors(web.json_response({"error": "invalid"}, status=400))

    # Объект пользователя для save_request
    class _U:
        id = user["id"]
        username = user.get("username")
        full_name = (user.get("first_name", "") + " " + user.get("last_name", "")).strip() or None

    data = {"problem": problem, "district": district, "address": address,
            "urgency": urgency, "phone": phone}
    request_id = database.save_request(data, _U())
    await sync_new(request_id)
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
    register_vk_routes(app)
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
        ph = r.get("phone", "")
        d["phone"] = requests_core.format_ru_phone(ph)   # красивый показ
        d["phone_dial"] = requests_core.phone_for_dial(ph)  # для tel:
        d["username"] = r.get("username") or ""
        d["full_name"] = r.get("full_name") or ""
        source = r.get("source") or "tg"
        d["source"] = source
        if source == "vk":
            d["client_link"] = f"https://vk.com/id{r.get('user_id')}"
            d["client_label"] = f"Профиль ВК"
        elif r.get("username"):
            d["client_link"] = f"https://t.me/{r.get('username')}"
            d["client_label"] = "@" + r.get("username")
        else:
            d["client_link"] = ""
            d["client_label"] = r.get("full_name") or ""
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
    elif action == "release":
        ok = database.release_request(rid, mid)
    else:  # complete
        ok = database.complete_request(rid, mid)

    if not ok:
        return _cors(web.json_response({"error": "not_allowed"}, status=403))
    req = database.get_request(rid)
    if req:
        await sync_update(req)
    return _cors(web.json_response({"ok": True}))


# ================= ВКонтакте (мини-приложение) =================
# Проверка подписи параметров запуска VK Mini App и эндпоинты для ВК-клиента/мастера.

import base64
from urllib.parse import urlencode


def _check_vk_sign(query: str) -> dict | None:
    """Проверяет подпись параметров запуска VK Mini App.
    query — строка параметров (то, что после ? в URL запуска).
    Возвращает dict параметров (vk_user_id и т.д.) если подпись верна, иначе None."""
    if not config.VK_SECRET:
        return None
    try:
        params = dict(parse_qsl(query, keep_blank_values=True))
    except Exception:
        return None
    sign = params.get("sign")
    if not sign:
        return None
    # Берём только vk_-параметры, сортируем, собираем query-строку
    vk_params = {k: v for k, v in params.items() if k.startswith("vk_")}
    if not vk_params:
        return None
    ordered = sorted(vk_params.items())
    check_string = urlencode(ordered)
    digest = hmac.new(config.VK_SECRET.encode(), check_string.encode(), hashlib.sha256).digest()
    calc_sign = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(calc_sign, sign):
        return None
    return params


def _vk_user_id(query: str) -> str | None:
    params = _check_vk_sign(query)
    if not params:
        return None
    return params.get("vk_user_id")


def _vk_role(vk_id: str) -> str:
    """Возвращает роль пользователя ВК: admin / master / client."""
    sid = str(vk_id)
    if sid in [str(a) for a in config.VK_ADMIN_IDS]:
        return "admin"
    if sid in [str(m) for m in config.VK_MASTER_IDS]:
        return "master"
    return "client"


async def vk_me(request: web.Request) -> web.Response:
    """Возвращает роль пользователя (для выбора экрана в мини-аппе ВК)."""
    vk_id = _vk_user_id(request.query.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    return _cors(web.json_response({"vk_id": vk_id, "role": _vk_role(vk_id)}))


def make_vk_user(vk_id: str):
    """Псевдо-объект пользователя для save_request (ВК-клиент)."""
    class _U:
        id = int(vk_id)
        username = None
        full_name = f"VK id{vk_id}"
    return _U()


# ----- ВК: эндпоинты заявок (клиент) -----

async def vk_my_requests(request: web.Request) -> web.Response:
    vk_id = _vk_user_id(request.query.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rows = database.get_user_requests(int(vk_id))
    return _cors(web.json_response({"requests": [_req_to_dict(r) for r in rows]}))


async def vk_create(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    problem = str(body.get("problem", "")).strip()
    district = str(body.get("district", "")).strip()
    address = str(body.get("address", "")).strip()
    urgency = str(body.get("urgency", "")).strip()
    phone = requests_core.normalize_ru_phone(str(body.get("phone", "")).strip())
    DISTRICTS = ["Индустриальный", "Северный", "Заягорбский", "Зашекснинский", "Пригород"]
    URGENCIES = ["Срочно — авария", "Сегодня", "В ближайшие дни"]
    if (len(problem) < 3 or district not in DISTRICTS or len(address) < 3
            or urgency not in URGENCIES or phone is None):
        return _cors(web.json_response({"error": "invalid"}, status=400))
    data = {"problem": problem, "district": district, "address": address,
            "urgency": urgency, "phone": phone}
    request_id = database.save_request(data, make_vk_user(vk_id), source="vk")
    await sync_new(request_id)
    return _cors(web.json_response({"ok": True, "id": request_id}))


async def vk_edit(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rid = body.get("id")
    fields = body.get("fields", {})
    if not isinstance(rid, int) or not isinstance(fields, dict):
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    if "phone" in fields and fields["phone"]:
        norm = requests_core.normalize_ru_phone(str(fields["phone"]))
        if norm is None:
            return _cors(web.json_response({"error": "invalid_phone"}, status=400))
        fields["phone"] = norm
    status = database.update_request(rid, int(vk_id), fields)
    if status is None:
        return _cors(web.json_response({"error": "not_editable"}, status=403))
    req = database.get_request(rid)
    if req:
        await sync_update(req)
    return _cors(web.json_response({"ok": True, "request": _req_to_dict(req)}))


async def vk_delete(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rid = body.get("id")
    if not isinstance(rid, int):
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    before = database.get_request(rid)
    taker_id = before["taken_by_id"] if before and before["status"] == "taken" else None
    ok = database.cancel_request(rid, int(vk_id))
    if not ok:
        return _cors(web.json_response({"error": "not_found"}, status=404))
    req = database.get_request(rid)
    if req:
        await sync_update(req)  # уберёт пинги в обоих каналах + статус клиенту
        if taker_id and _master_bot is not None:
            await requests_core.notify_master_canceled(_master_bot, taker_id, req)
    return _cors(web.json_response({"ok": True}))


# ----- ВК: эндпоинты мастера -----

def _vk_is_master(vk_id: str) -> bool:
    return _vk_role(vk_id) in ("master", "admin")


async def vk_m_board(request: web.Request) -> web.Response:
    vk_id = _vk_user_id(request.query.get("sign_params", ""))
    if not vk_id or not _vk_is_master(vk_id):
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rows = database.get_open_requests()
    return _cors(web.json_response({"requests": [_master_card_dict(r, False) for r in rows]}))


async def vk_m_mine(request: web.Request) -> web.Response:
    vk_id = _vk_user_id(request.query.get("sign_params", ""))
    if not vk_id or not _vk_is_master(vk_id):
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    active = database.get_master_active(int(vk_id))
    history = database.get_master_history(int(vk_id))
    return _cors(web.json_response({
        "active": [_master_card_dict(r, True) for r in active],
        "history": [_master_card_dict(r, True) for r in history],
    }))


async def vk_m_action(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id or not _vk_is_master(vk_id):
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    rid = body.get("id")
    action = body.get("action")
    if not isinstance(rid, int) or action not in ("take", "release", "complete"):
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    mid = int(vk_id)
    mname = f"VK id{vk_id}"
    if action == "take":
        ok = database.take_request(rid, mid, mname)
    elif action == "release":
        ok = database.release_request(rid, mid)
    else:
        ok = database.complete_request(rid, mid)
    if not ok:
        return _cors(web.json_response({"error": "not_allowed"}, status=403))
    req = database.get_request(rid)
    if req:
        await sync_update(req)
    return _cors(web.json_response({"ok": True}))


async def vk_m_clear_history(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id or not _vk_is_master(vk_id):
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    n = database.clear_master_history(int(vk_id))
    return _cors(web.json_response({"ok": True, "cleared": n}))


async def vk_allow(request: web.Request) -> web.Response:
    """Клиент/мастер разрешил сообщения от сообщества — записываем."""
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad_request"}, status=400))
    vk_id = _vk_user_id(body.get("sign_params", ""))
    if not vk_id:
        return _cors(web.json_response({"error": "unauthorized"}, status=401))
    allowed = bool(body.get("allowed", True))
    database.vk_set_allowed(int(vk_id), allowed)
    return _cors(web.json_response({"ok": True}))


def register_vk_routes(app: web.Application) -> None:
    app.router.add_get("/api/vk/me", vk_me)
    app.router.add_post("/api/vk/allow", vk_allow)
    app.router.add_get("/api/vk/my_requests", vk_my_requests)
    app.router.add_post("/api/vk/create", vk_create)
    app.router.add_post("/api/vk/edit", vk_edit)
    app.router.add_post("/api/vk/delete", vk_delete)
    app.router.add_get("/api/vk/m/board", vk_m_board)
    app.router.add_get("/api/vk/m/mine", vk_m_mine)
    app.router.add_post("/api/vk/m/action", vk_m_action)
    app.router.add_post("/api/vk/m/clear_history", vk_m_clear_history)
    for path in ("/api/vk/me", "/api/vk/allow", "/api/vk/my_requests", "/api/vk/create", "/api/vk/edit",
                 "/api/vk/delete", "/api/vk/m/board", "/api/vk/m/mine",
                 "/api/vk/m/action", "/api/vk/m/clear_history"):
        app.router.add_options(path, handle_options)
