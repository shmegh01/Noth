#!/usr/bin/env python3
# language: Python 3.13, file: secretary.py, runtime: pyTelegramBotAPI
# Основные зависимости: pyTelegramBotAPI Flask
# Set BOT_TOKEN in the environment before starting; do not hard-code secrets.

import telebot
import logging
import json
import os
import threading
import time as _time
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from collections import OrderedDict

BOT_TOKEN = "8997436667:AAG7NsZPabd_j2HPpSwoiQM73A3QGMiV8yA"

BOT_TAG   = "@SpaceSpybot"
MAX_CACHE  = 20_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("spacespy")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ── Web-сервер (keep-alive + статус) ─────────────────────────────────────────
_flask_app    = Flask(__name__)
_start_time   = _time.time()

_STATUS_HTML = """\
<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>SpaceSpy</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0a;color:#e0e0e0;font-family:"Segoe UI",system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#111;border:1px solid #1f1f1f;border-radius:20px;
       padding:44px 52px;max-width:460px;width:calc(100% - 32px);text-align:center}}
.pulse{{display:inline-block;width:9px;height:9px;border-radius:50%;
        background:#22c55e;box-shadow:0 0 0 0 #22c55e44;
        animation:pulse 2s infinite;margin-right:10px;vertical-align:middle}}
@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 #22c55e55}}50%{{box-shadow:0 0 0 8px transparent}}}}
h1{{font-size:1.65rem;font-weight:600;letter-spacing:-.4px;margin-bottom:6px}}
.sub{{color:#555;font-size:.82rem;margin-bottom:36px}}
.row{{display:flex;justify-content:space-between;align-items:center;
      padding:13px 0;border-bottom:1px solid #1a1a1a}}
.row:last-of-type{{border:none}}
.lbl{{color:#666;font-size:.82rem}}
.val{{font-size:.9rem;font-weight:500}}
.hint{{color:#333;font-size:.72rem;margin-top:28px}}
</style></head>
<body><div class="card">
<h1><span class="pulse"></span>SpaceSpy</h1>
<p class="sub">Telegram Business Bot</p>
<div class="row"><span class="lbl">Статус</span><span class="val">🟢&nbsp;Online</span></div>
<div class="row"><span class="lbl">Аптайм</span><span class="val">{uptime}</span></div>
<div class="row"><span class="lbl">Подключений</span><span class="val">{conns}</span></div>
<div class="row"><span class="lbl">Кэш</span><span class="val">{cache} сообщений</span></div>
<p class="hint">страница обновляется каждые 30&nbsp;с</p>
</div></body></html>"""

@_flask_app.route("/")
def _web_index():
    secs = int(_time.time() - _start_time)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    uptime = f"{h}ч {m}м {s}с" if h else f"{m}м {s}с"
    return _STATUS_HTML.format(uptime=uptime, conns=len(connections), cache=len(_cache))

def _run_flask():
    port = int(os.environ.get("PORT", 8080))
    log.info(f"Web server on :{port}")
    _flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


# ── Хранилища ─────────────────────────────────────────────────────────────────
connections: dict[str, int] = {}
_cache: OrderedDict[tuple, dict] = OrderedDict()

# Настройки уведомлений сохраняются отдельно для каждого владельца.
SETTINGS_FILE = os.environ.get(
    "BOT_SETTINGS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_settings.json"),
)
_SETTINGS_LOCK = threading.RLock()
_DEFAULT_SETTINGS = {"delete": True, "edit": True, "save": True}

def _load_settings() -> dict[int, dict]:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as settings_file:
            stored = json.load(settings_file)
        if not isinstance(stored, dict):
            return {}
        return {
            int(owner_id): {
                "delete": bool(values.get("delete", True)),
                "edit": bool(values.get("edit", True)),
                "save": bool(values.get("save", True)),
            }
            for owner_id, values in stored.items()
            if isinstance(values, dict)
        }
    except (OSError, ValueError, TypeError):
        return {}

user_settings: dict[int, dict] = _load_settings()
# Изменения сначала попадают в черновик и применяются после нажатия «Сохранить».
draft_settings: dict[int, dict] = {}

def get_settings(owner_id: int) -> dict:
    with _SETTINGS_LOCK:
        if owner_id not in user_settings:
            user_settings[owner_id] = dict(_DEFAULT_SETTINGS)
        return user_settings[owner_id]

def get_draft_settings(owner_id: int) -> dict:
    with _SETTINGS_LOCK:
        if owner_id not in draft_settings:
            draft_settings[owner_id] = dict(get_settings(owner_id))
        return draft_settings[owner_id]

def save_settings(owner_id: int) -> None:
    with _SETTINGS_LOCK:
        draft = draft_settings.get(owner_id)
        if draft is None:
            return
        updated = {
            "delete": bool(draft["delete"]),
            "edit": bool(draft["edit"]),
            "save": bool(draft.get("save", True)),
        }
        candidate = dict(user_settings)
        candidate[owner_id] = updated
        os.makedirs(os.path.dirname(os.path.abspath(SETTINGS_FILE)), exist_ok=True)
        temp_path = SETTINGS_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as settings_file:
            json.dump(
                {str(uid): values for uid, values in candidate.items()},
                settings_file,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(temp_path, SETTINGS_FILE)
        user_settings[owner_id] = updated
        draft_settings.pop(owner_id, None)
        if not updated["save"]:
            owner_connections = {
                connection_id
                for connection_id, connected_owner in connections.items()
                if connected_owner == owner_id
            }
            for key in list(_cache):
                if key[0] in owner_connections:
                    _cache.pop(key, None)

# ── Кэш ───────────────────────────────────────────────────────────────────────
def cache_put(bc_id, chat_id, msg_id, data):
    key = (bc_id, chat_id, msg_id)
    if key in _cache:
        _cache.move_to_end(key)
        _cache[key] = data
        return
    if len(_cache) >= MAX_CACHE:
        _cache.popitem(last=False)
    _cache[key] = data

def cache_get(bc_id, chat_id, msg_id):
    return _cache.get((bc_id, chat_id, msg_id))

def cache_pop(bc_id, chat_id, msg_id):
    return _cache.pop((bc_id, chat_id, msg_id), None)

# ── Парсинг ───────────────────────────────────────────────────────────────────
_MEDIA = [
    ("photo",      "📷 Фото"),
    ("video",      "🎬 Видео"),
    ("voice",      "🎤 Голосовое сообщение"),
    ("video_note", "⭕ Кружок"),
    ("audio",      "🎵 Аудио"),
    ("animation",  "🌀 GIF"),
    ("location",   "📍 Геопозиция"),
]

def extract_text(msg):
    if msg.text:    return msg.text
    if msg.caption: return msg.caption
    for attr, label in _MEDIA:
        if getattr(msg, attr, None):
            return label
    if getattr(msg, "sticker", None):
        return f"🎭 Стикер {msg.sticker.emoji or ''}"
    if getattr(msg, "document", None):
        return f"📎 {msg.document.file_name or 'Документ'}"
    if getattr(msg, "poll", None):
        return f"📊 Опрос: {msg.poll.question}"
    if getattr(msg, "contact", None):
        return f"👤 {msg.contact.first_name}"
    return "❓ Сообщение без текста"

def get_name(msg):
    if msg.from_user:
        u = msg.from_user
        parts = [p for p in [u.first_name, u.last_name] if p]
        return " ".join(parts) or "Unknown"
    if getattr(msg, "sender_chat", None):
        return msg.sender_chat.title or "Unknown"
    return "Unknown"

def get_uid(msg):
    if msg.from_user:    return msg.from_user.id
    if getattr(msg, "sender_chat", None): return msg.sender_chat.id
    return 0

def get_chat_title(chat):
    return (
        getattr(chat, "title", None)
        or getattr(chat, "username", None)
        or getattr(chat, "first_name", None)
        or str(chat.id)
    )

# ── Форматирование ────────────────────────────────────────────────────────────
def fmt_deleted(name, uid, text):
    return (
        f"<b>{name}</b> [{uid}] <b>удалил(а) сообщение</b>\n\n"
        f"Текст:\n"
        f"<blockquote>{text}</blockquote>\n\n"
        f"{BOT_TAG}"
    )

def fmt_edited(name, uid, old_text, new_text):
    return (
        f"<b>{name}</b> [{uid}] <b>изменил(а) сообщение</b>\n\n"
        f"Было:\n"
        f"<blockquote>{old_text}</blockquote>\n\n"
        f"Стало:\n"
        f"<blockquote>{new_text}</blockquote>\n\n"
        f"{BOT_TAG}"
    )

def fmt_circle(name, uid):
    return (
        f"🔴 <b>{name}</b> [{uid}] <b>прислал(а) одноразовый кружок</b>\n\n"
        f"{BOT_TAG}"
    )

# ── Клавиатуры ────────────────────────────────────────────────────────────────
def kb_main():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("❓ Как подключить бота?", callback_data="how"))
    kb.add(InlineKeyboardButton("⚙️ Настройки",            callback_data="settings"))
    kb.add(InlineKeyboardButton("💬 Поддержка",            url="https://t.me/javasvin"))
    return kb

def kb_settings(owner_id: int):
    s  = get_draft_settings(owner_id)
    d  = "🟢" if s["delete"] else "🔴"
    e  = "🟢" if s["edit"]   else "🔴"
    sv = "🟢" if s.get("save", True) else "🔴"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Уведомить об удалении | {d}",  callback_data="toggle_delete"))
    kb.add(InlineKeyboardButton(f"Уведомить об изменении | {e}", callback_data="toggle_edit"))
    kb.add(InlineKeyboardButton(f"Сохранять сообщения в памяти бота | {sv}", callback_data="toggle_save"))
    kb.add(InlineKeyboardButton("💾 Сохранить", callback_data="save_settings"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back"))
    return kb

# ── /start ────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    text = (
        "👁 <b>Добро пожаловать в SpaceSpy!</b>\n\n"
        "Ваш инструмент для отслеживания активности в Telegram-чатах. "
        "Отслеживайте важные изменения и ключевые события.\n\n"
        "<i>Инструкция по подключению на фото!</i>"
    )
    bot.send_message(msg.chat.id, text, reply_markup=kb_main())

# ── Callbacks ─────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "how")
def cb_how(call):
    text = (
        "📋 <b>Как подключить бота:</b>\n\n"
        "1️⃣ Откройте <b>Настройки</b> профиля → нажмите <b>«Изм.»</b>\n\n"
        "2️⃣ Прокрутите вниз → найдите <b>«Автоматизация чатов»</b>\n\n"
        f"3️⃣ Введите <b>{BOT_TAG}</b> и нажмите <b>«Добавить»</b>\n\n"
        "✅ Готово — бот подключён!"
    )
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("⬅️ Назад", callback_data="back")
        )
    )

@bot.callback_query_handler(func=lambda c: c.data == "settings")
def cb_settings(call):
    bot.answer_callback_query(call.id)
    draft_settings[call.from_user.id] = dict(get_settings(call.from_user.id))
    bot.edit_message_text(
        "⚙️ <b>Настройки уведомлений</b>\n\n"
        "Сохраняемые сообщения нужны для сравнения старого и нового текста. "
        "Кэш ограничен 20 000 сообщениями и очищается при перезапуске.\n"
        "Изменения настроек применяются после нажатия «Сохранить».",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "toggle_delete")
def cb_toggle_delete(call):
    s = get_draft_settings(call.from_user.id)
    s["delete"] = not s["delete"]
    state = "включены 🟢" if s["delete"] else "выключены 🔴"
    bot.answer_callback_query(call.id, f"Черновик: уведомления об удалении {state}. Нажмите «Сохранить».")
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "toggle_edit")
def cb_toggle_edit(call):
    s = get_draft_settings(call.from_user.id)
    s["edit"] = not s["edit"]
    state = "включены 🟢" if s["edit"] else "выключены 🔴"
    bot.answer_callback_query(call.id, f"Черновик: уведомления об изменении {state}. Нажмите «Сохранить».")
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "toggle_save")
def cb_toggle_save(call):
    s = get_draft_settings(call.from_user.id)
    s["save"] = not s.get("save", True)
    state = "включено 🟢" if s["save"] else "выключено 🔴"
    bot.answer_callback_query(call.id, f"Сохранение сообщений {state}. Нажмите «Сохранить».")
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "save_settings")
def cb_save_settings(call):
    try:
        save_settings(call.from_user.id)
    except OSError as exc:
        log.error("settings save failed: %s", exc)
        bot.answer_callback_query(call.id, "Не удалось сохранить настройки", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Настройки сохранены")
    bot.edit_message_text(
        "⚙️ <b>Настройки уведомлений сохранены</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id),
    )

@bot.callback_query_handler(func=lambda c: c.data == "back")
def cb_back(call):
    bot.answer_callback_query(call.id)
    text = (
        "👁 <b>Добро пожаловать в SpaceSpy!</b>\n\n"
        "Ваш инструмент для отслеживания активности в Telegram-чатах. "
        "Отслеживайте важные изменения и ключевые события.\n\n"
        "<i>Инструкция по подключению на фото!</i>"
    )
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_main()
    )

# ── Business handlers ─────────────────────────────────────────────────────────
@bot.business_connection_handler()
def on_bc(bc):
    if bc.is_enabled:
        connections[bc.id] = bc.user.id
        log.info(f"connected: {bc.id} → {bc.user.id}")
    else:
        connections.pop(bc.id, None)

@bot.business_message_handler()
def on_business_msg(msg):
    bc_id = getattr(msg, "business_connection_id", None)
    if not bc_id:
        return

    owner = connections.get(bc_id)
    vn    = getattr(msg, "video_note", None)

    if vn and owner:
        try:
            bot.send_message(owner, fmt_circle(get_name(msg), get_uid(msg)), disable_notification=True)
            bot.send_video_note(owner, vn.file_id, disable_notification=True)
        except Exception as e:
            log.error(f"circle: {e}")

    if owner and get_settings(owner).get("save", True):
        cache_put(bc_id, msg.chat.id, msg.message_id, {
            "text": extract_text(msg),
            "name": get_name(msg),
            "uid":  get_uid(msg),
        })

@bot.edited_business_message_handler()
def on_edited(msg):
    bc_id = getattr(msg, "business_connection_id", None)
    if not bc_id:
        return

    owner    = connections.get(bc_id)
    saving   = bool(owner and get_settings(owner).get("save", True))
    old      = cache_get(bc_id, msg.chat.id, msg.message_id) if saving else None
    new_text = extract_text(msg)

    if old and owner and old["text"] != new_text:
        s = get_settings(owner)
        if s["edit"]:
            try:
                bot.send_message(
                    owner,
                    fmt_edited(old["name"], old["uid"], old["text"], new_text),
                    disable_notification=True,
                )
            except Exception as e:
                log.error(f"edited: {e}")

    if saving:
        cache_put(bc_id, msg.chat.id, msg.message_id, {
            "text": new_text,
            "name": get_name(msg),
            "uid":  get_uid(msg),
        })

@bot.deleted_business_messages_handler()
def on_deleted(event):
    bc_id   = event.business_connection_id
    chat_id = event.chat.id
    owner   = connections.get(bc_id)

    if owner is None:
        try:
            bc    = bot.get_business_connection(bc_id)
            owner = bc.user.id
            connections[bc_id] = owner
        except Exception as e:
            log.error(f"get_bc: {e}")
            return

    s = get_settings(owner)
    if not s["delete"]:
        return

    for msg_id in event.message_ids:
        data = cache_pop(bc_id, chat_id, msg_id) if s.get("save", True) else None

        text = fmt_deleted(data["name"], data["uid"], data["text"]) if data else (
            f"🗑 Удалено сообщение · не кэшировано\n\n{BOT_TAG}"
        )

        try:
            bot.send_message(owner, text, disable_notification=True)
        except Exception as e:
            log.error(f"deleted: {e}")

# ── Entry ─────────────────────────────────────────────────────────────────────
threading.Thread(target=_run_flask, daemon=True).start()
log.info("SpaceSpy started")
bot.infinity_polling(
    allowed_updates=[
        "message",
        "callback_query",
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ],
    logger_level=logging.INFO,
)
