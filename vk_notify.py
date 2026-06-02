"""Уведомления в ВКонтакте через сообщество (messages.send/edit/delete).
Чистый диалог: клиенту — одно сообщение-статус на заявку (редактируем при смене статуса);
мастерам — пинги о свободных заявках (удаляем, когда заявка занята/отменена)."""

import logging
import random
import urllib.parse
import urllib.request
import json

import config
import database
import requests_core

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/"
VK_V = "5.131"


def _call(method: str, params: dict) -> dict | None:
    """Вызов метода VK API с токеном сообщества. Возвращает поле response или None."""
    if not config.VK_GROUP_TOKEN:
        return None
    params = dict(params)
    params["access_token"] = config.VK_GROUP_TOKEN
    params["v"] = VK_V
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(VK_API + method, data=data, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        if "error" in body:
            logger.warning("VK API error %s: %s", method, body["error"].get("error_msg"))
            return None
        return body.get("response")
    except Exception as e:
        logger.warning("VK API call failed %s: %s", method, e)
        return None


def send(vk_id: int, text: str) -> int | None:
    """Шлёт сообщение пользователю. Возвращает message_id или None."""
    resp = _call("messages.send", {
        "peer_id": vk_id,
        "message": text,
        "random_id": random.randint(1, 2_000_000_000),
    })
    # messages.send для лички возвращает message_id (число)
    if isinstance(resp, int):
        return resp
    return None


def edit(vk_id: int, message_id: int, text: str) -> bool:
    resp = _call("messages.edit", {
        "peer_id": vk_id,
        "message_id": message_id,
        "message": text,
    })
    return resp == 1


def delete(vk_id: int, message_id: int) -> bool:
    resp = _call("messages.delete", {
        "message_ids": message_id,
        "delete_for_all": 1,
    })
    return resp is not None


# ---------- Пинги мастерам ВК ----------

def _ping_text(req: dict) -> str:
    return (
        f"🆕 Новая заявка №{req['id']}\n"
        f"📍 — {req['district']} · ⏱ — {req['urgency']}\n"
        f"Откройте «Доску заявок» в приложении, чтобы принять."
    )


def send_master_pings(request_id: int) -> None:
    """Пинг всем ВК-мастерам (которые разрешили сообщения)."""
    req = database.get_request(request_id)
    if not req or req["status"] != "new":
        return
    text = _ping_text(req)
    for mid in config.VK_MASTER_IDS:
        mid_int = int(mid)
        if not database.vk_is_allowed(mid_int):
            continue
        msg_id = send(mid_int, text)
        if msg_id:
            database.vk_save_master_ping(request_id, mid_int, msg_id)


def remove_master_pings(request_id: int) -> None:
    for vk_id, message_id in database.vk_get_master_pings(request_id):
        delete(vk_id, message_id)
    database.vk_clear_master_pings(request_id)


# ---------- Статус клиенту ВК (чистый диалог) ----------

def _client_status_text(req: dict) -> str:
    status = req["status"]
    extra = ""
    if status == "new":
        line = "🔄🔍 Снова ищем мастера" if req.get("released_once") else "🔍 Ищем мастера"
    elif status == "taken":
        line = "✅ Нашли для вас мастера"
        extra = "\n\nМастер скоро свяжется с вами ✨"
    elif status == "done":
        line = "🏁 Заявка выполнена"
    else:
        line = status
    return (
        f"Заявка №{req['id']} · {line}\n\n"
        f"🛠 — {req['problem']}\n"
        f"📍 — {req['district']}, {req['address'] or '—'}\n"
        f"⏱ — {req['urgency']}\n"
        f"📞 — {requests_core.format_ru_phone(req['phone'])}"
        + extra
    )


def refresh_client_status(req: dict) -> None:
    """Одно сообщение-статус на заявку: редактируем при смене статуса.
    canceled — удаляем. Работает только если клиент разрешил сообщения."""
    vk_id = req.get("user_id")
    if not vk_id or not database.vk_is_allowed(int(vk_id)):
        return
    rid = req["id"]
    prev = database.vk_get_client_status(rid)

    if req["status"] == "canceled":
        if prev:
            delete(prev[0], prev[1])
            database.vk_delete_client_status(rid)
        return

    text = _client_status_text(req)
    if prev:
        # редактируем существующее сообщение
        ok = edit(prev[0], prev[1], text)
        if ok:
            return
        # если не получилось отредактировать — удалим и пошлём заново
        delete(prev[0], prev[1])
        database.vk_delete_client_status(rid)
    msg_id = send(int(vk_id), text)
    if msg_id:
        database.vk_save_client_status(rid, int(vk_id), msg_id)
