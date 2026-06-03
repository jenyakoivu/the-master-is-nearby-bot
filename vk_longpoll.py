"""Асинхронный слушатель событий сообщества ВК (Bots Long Poll).
Ловит нажатия inline callback-кнопок (message_event) и обрабатывает их —
в частности, кнопку «Убрать» под уведомлением об отмене заявки.

Работает на чистом aiohttp в общем event loop, не блокируя ботов."""

import asyncio
import json
import logging

import aiohttp

import config
import database
import vk_notify

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/"
VK_V = "5.131"


async def _api(session: aiohttp.ClientSession, method: str, params: dict) -> dict | None:
    params = dict(params)
    params["access_token"] = config.VK_GROUP_TOKEN
    params["v"] = VK_V
    try:
        async with session.post(VK_API + method, data=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.json()
        if "error" in body:
            logger.warning("VK LP %s error: %s", method, body["error"].get("error_msg"))
            return None
        return body.get("response")
    except Exception as e:
        logger.warning("VK LP %s failed: %s", method, e)
        return None


async def _answer_event(session, event_id, user_id, peer_id, snackbar_text=None):
    """Отвечает на нажатие callback-кнопки (обязательно, иначе у пользователя крутится загрузка)."""
    event_data = {}
    if snackbar_text:
        event_data = {"type": "show_snackbar", "text": snackbar_text}
    await _api(session, "messages.sendMessageEventAnswer", {
        "event_id": event_id,
        "user_id": user_id,
        "peer_id": peer_id,
        "event_data": json.dumps(event_data) if event_data else "",
    })


async def _handle_message_event(session, obj):
    """Обрабатывает нажатие callback-кнопки."""
    payload = obj.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    user_id = obj.get("user_id")
    peer_id = obj.get("peer_id")
    event_id = obj.get("event_id")
    action = payload.get("action")

    if action == "del_cancel_notice":
        rid = payload.get("rid")
        # удаляем сообщение-уведомление
        msg_id = database.vk_get_cancel_notice(rid, user_id) if rid else None
        if msg_id:
            vk_notify.delete(user_id, msg_id)
            database.vk_delete_cancel_notice(rid, user_id)
        await _answer_event(session, event_id, user_id, peer_id, "Убрано")
    else:
        await _answer_event(session, event_id, user_id, peer_id)


async def run_longpoll():
    """Главный цикл Long Poll. Запускать как фоновую задачу."""
    if not config.VK_GROUP_TOKEN or not config.VK_GROUP_ID:
        logger.info("ВК Long Poll не запущен: нет токена или ID сообщества")
        return
    group_id = str(config.VK_GROUP_ID).lstrip("-")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # 1) получаем сервер Long Poll
                srv = await _api(session, "groups.getLongPollServer", {"group_id": group_id})
                if not srv:
                    await asyncio.sleep(10)
                    continue
                server = srv["server"]
                key = srv["key"]
                ts = srv["ts"]
                # 2) слушаем события
                while True:
                    try:
                        async with session.get(server, params={
                            "act": "a_check", "key": key, "ts": ts, "wait": 25,
                        }, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                            data = await resp.json()
                    except Exception as e:
                        logger.warning("VK LP poll error: %s", e)
                        break  # переполучим сервер
                    # обработка возможных ошибок ts/key
                    if "failed" in data:
                        if data["failed"] == 1:
                            ts = data.get("ts", ts)
                            continue
                        else:
                            break  # 2/3 — переполучить сервер/ключ
                    ts = data.get("ts", ts)
                    for ev in data.get("updates", []):
                        if ev.get("type") == "message_event":
                            await _handle_message_event(session, ev.get("object", {}))
            except Exception as e:
                logger.warning("VK LP loop error, restart in 10s: %s", e)
                await asyncio.sleep(10)
