#!/usr/bin/env python3
# language: Python 3.13, file: secretary.py, runtime: pyTelegramBotAPI
# Основные зависимости: pyTelegramBotAPI requests beautifulsoup4 phonenumbers
# Maigret необязателен; в Pydroid/Android его нативные зависимости могут не собраться.
# Set BOT_TOKEN in the environment before starting; do not hard-code secrets.

import telebot
import logging
import html
import os
import re
import requests
import threading
import time as _time
import urllib.parse
from flask import Flask
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import phonenumbers
from phonenumbers import carrier as ph_carrier, geocoder as ph_geocoder
from phonenumbers import number_type as ph_num_type, PhoneNumberType

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8997436667:AAG7NsZPabd_j2HPpSwoiQM73A3QGMiV8yA")
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

# owner_id → {"delete": bool, "edit": bool}
user_settings: dict[int, dict] = {}

def get_settings(owner_id: int) -> dict:
    if owner_id not in user_settings:
        user_settings[owner_id] = {"delete": True, "edit": True}
    return user_settings[owner_id]

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
    s  = get_settings(owner_id)
    d  = "🟢" if s["delete"] else "🔴"
    e  = "🟢" if s["edit"]   else "🔴"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Уведомить об удалении | {d}",  callback_data="toggle_delete"))
    kb.add(InlineKeyboardButton(f"Уведомить об изменении | {e}", callback_data="toggle_edit"))
    kb.add(InlineKeyboardButton("⬅️ Назад",                      callback_data="back"))
    return kb

# ── OSINT .info ───────────────────────────────────────────────────────────────
_SCRAPE_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "DNT": "1",
}
_TIMEOUT = (3.05, 7)

_HIDDEN_RESULT_HOSTS = {
    "google.com", "google.ru", "bing.com", "duckduckgo.com",
    "yandex.ru", "yandex.com", "nomerorg.com", "zvonili.com",
    "kto-zvonit.com",
}


def _public_result_url(raw_url: str, base_url: str = "") -> str | None:
    """Unwrap search redirects and keep direct, useful result destinations."""
    try:
        url = urllib.parse.urljoin(base_url, raw_url.strip())
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
            url = params.get("uddg", [url])[0]
        elif parsed.hostname and parsed.hostname.endswith("yandex.ru"):
            url = params.get("url", params.get("target", [url]))[0]
        elif parsed.hostname and parsed.hostname.endswith("google.com") and parsed.path == "/url":
            url = params.get("q", params.get("url", [url]))[0]

        target = urllib.parse.urlparse(url)
        host = (target.hostname or "").lower().removeprefix("www.")
        if target.scheme not in ("http", "https") or not host or not "." in host:
            return None
        if host.startswith(("google.", "bing.", "duckduckgo.", "yandex.")):
            return None
        if any(host == hidden or host.endswith("." + hidden) for hidden in _HIDDEN_RESULT_HOSTS):
            return None
        # Drop fragments/tracking query strings in the visible destination while
        # retaining the original path needed to open a profile or page.
        clean_path = target.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit((target.scheme, target.netloc, clean_path, "", ""))[:500]
    except (ValueError, TypeError):
        return None


def _normalize_phone(raw: str) -> str | None:
    """Normalize a valid phone to E.164; assume Russia for local numbers."""
    value = raw.strip()
    # Russian national dialing often uses 8 as the trunk prefix.
    digits = re.sub(r"\D", "", value)
    if not value.startswith("+") and len(digits) == 11 and digits.startswith("8"):
        value = "+7" + digits[1:]
    try:
        parsed = phonenumbers.parse(value, "RU")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


def _normalize_username(raw: str) -> str | None:
    """Accept a Telegram username, @username, or a public t.me link."""
    value = raw.strip()
    match = re.fullmatch(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,32})/?", value, re.I)
    s = match.group(1) if match else value.lstrip("@")
    return s if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", s) else None


# ── src 1: phonenumbers (local, zero network) ─────────────────────────────────
def _src_phonenumbers_local(phone: str) -> dict:
    try:
        p = phonenumbers.parse(phone)
        if not phonenumbers.is_valid_number(p):
            return {"source": "📡 Анализ номера", "Статус": "Номер невалиден"}

        type_map = {
            PhoneNumberType.MOBILE:              "📱 Мобильный",
            PhoneNumberType.FIXED_LINE:           "📞 Стационарный",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "📱/📞 Мобильный или стационарный",
            PhoneNumberType.TOLL_FREE:            "🆓 Бесплатный",
            PhoneNumberType.VOIP:                 "🌐 VoIP",
            PhoneNumberType.PREMIUM_RATE:         "💰 Платный",
        }
        op  = ph_carrier.name_for_number(p, "ru")
        geo = ph_geocoder.description_for_number(p, "ru")

        return {
            "source":   "📡 Анализ номера",
            "Оператор": op  or "—",
            "Регион":   geo or "—",
            "Тип":      type_map.get(ph_num_type(p), "Другой"),
            "E.164":    phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "Валиден":  "✅ Да",
        }
    except Exception as exc:
        return {"source": "📡 Анализ номера", "error": str(exc)}


# ── src 2: Telegram get_chat (username lookup) ────────────────────────────────
def _src_tg_username(username: str) -> dict:
    try:
        chat = bot.get_chat("@" + username)
        info: dict = {"source": "📱 Telegram"}

        names = [x for x in [
            getattr(chat, "first_name", None),
            getattr(chat, "last_name",  None),
        ] if x]
        if not names and getattr(chat, "title", None):
            names = [chat.title]

        if names:
            info["Имя"] = " ".join(names)
        info["ID"]  = str(chat.id)
        info["Тип"] = chat.type
        if getattr(chat, "username", None):
            info["Username"] = "@" + chat.username
            info["Ссылки"] = [f"https://t.me/{chat.username}"]
        if getattr(chat, "bio", None):
            info["Bio"] = chat.bio[:200]
            bio_links = re.findall(r"https?://[^\s<>]+", chat.bio)
            if bio_links:
                info.setdefault("Ссылки", []).extend(bio_links[:3])

        return info
    except Exception as exc:
        return {"source": "📱 Telegram", "error": f"Не найден — {exc}"}


# ── src 3: nomerorg.com ───────────────────────────────────────────────────────
def _src_nomerorg(phone: str) -> dict | None:
    try:
        clean = re.sub(r"\D", "", phone)
        r = requests.get(
            f"https://nomerorg.com/{clean}",
            headers=_SCRAPE_HDR, timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None

        soup   = BeautifulSoup(r.text, "html.parser")
        result: dict = {"source": "📋 NomerOrg"}

        h = soup.find("h1") or soup.find("h2")
        if h:
            txt = h.get_text(strip=True)
            # Only include if it's NOT just the phone number echoed back
            if txt and clean not in re.sub(r"\D", "", txt):
                result["Определён как"] = txt[:100]

        rating = soup.find(class_=re.compile(r"rate|rating|score|mark", re.I))
        if rating:
            result["Оценка"] = rating.get_text(strip=True)[:60]

        comments = soup.select(
            ".comment__text, .review-text, .comment-body, .user-comment, .text"
        )[:4]
        texts = [c.get_text(strip=True)[:120] for c in comments if c.get_text(strip=True)]
        if texts:
            result["Комментарии"] = texts

        return result if len(result) > 1 else None
    except Exception:
        return None


# ── src 4: zvonili.com ────────────────────────────────────────────────────────
def _src_zvonili(phone: str) -> dict | None:
    try:
        clean = re.sub(r"\D", "", phone)
        r = requests.get(
            f"https://zvonili.com/phone/{clean}",
            headers=_SCRAPE_HDR, timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None

        soup   = BeautifulSoup(r.text, "html.parser")
        result: dict = {"source": "📞 Zvonili"}

        cat = soup.find(class_=re.compile(r"categ|type|kind|label|badge", re.I))
        if cat:
            result["Категория"] = cat.get_text(strip=True)[:80]

        count_node = soup.find(string=re.compile(
            r"\d+\s*(жалоб|обращен|отзыв|звонк)", re.I
        ))
        if count_node:
            result["Активность"] = count_node.strip()[:80]

        paras = [
            p.get_text(strip=True)[:140]
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 35
        ][:3]
        if paras:
            result["Описание"] = paras

        return result if len(result) > 1 else None
    except Exception:
        return None


# ── src 5: kto-zvonit.com ─────────────────────────────────────────────────────
def _src_kto_zvonit(phone: str) -> dict | None:
    try:
        clean = re.sub(r"\D", "", phone)
        r = requests.get(
            f"https://kto-zvonit.com/{clean}",
            headers=_SCRAPE_HDR, timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None

        soup   = BeautifulSoup(r.text, "html.parser")
        result: dict = {"source": "☎️ Kto-Zvonit"}

        title = soup.find("h1")
        if title:
            txt = title.get_text(strip=True)
            if clean not in re.sub(r"\D", "", txt):
                result["Найдено"] = txt[:100]

        reviews = soup.select(".review, .comment, .feedback")[:3]
        texts = [rv.get_text(strip=True)[:100] for rv in reviews if rv.get_text(strip=True)]
        if texts:
            result["Отзывы"] = texts

        # Spam badge / rating label
        badge = soup.find(class_=re.compile(r"spam|danger|warn|safe|good", re.I))
        if badge:
            result["Пометка"] = badge.get_text(strip=True)[:60]

        return result if len(result) > 1 else None
    except Exception:
        return None


# ── src 6: Google snippet scrape ──────────────────────────────────────────────
def _src_google(query: str) -> dict | None:
    try:
        url = (
            "https://www.google.com/search?q="
            + urllib.parse.quote_plus(query)
            + "&hl=ru&num=6"
        )
        r = requests.get(url, headers=_SCRAPE_HDR, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None

        soup     = BeautifulSoup(r.text, "html.parser")
        snippets: list[str] = []

        for el in soup.select("div.VwiC3b, span.aCOpRe, div.IsZvec, div.lEBKkf"):
            t = el.get_text(strip=True)
            if t and len(t) > 25 and t not in snippets:
                snippets.append(t[:160])

        titles: list[str] = []
        links: list[str] = []
        for h3 in soup.select("h3.LC20lb, h3.r, h3")[:5]:
            t = h3.get_text(strip=True)
            if t and t not in titles:
                titles.append(t[:70])
            anchor = h3.find_parent("a")
            href = anchor.get("href", "") if anchor else ""
            direct = _public_result_url(href, "https://www.google.com")
            if direct and direct not in links:
                links.append(direct)

        if not snippets and not titles:
            return None

        result: dict = {"source": "🌐 Google"}
        if titles:
            result["Найдено на"] = titles
        if snippets:
            result["Сниппеты"]   = snippets[:3]
        if links:
            result["Ссылки"] = links[:4]
        return result
    except Exception:
        return None


def _src_duckduckgo(query: str) -> dict | None:
    """Search DuckDuckGo's lightweight HTML endpoint as a Google fallback."""
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query) + "&kl=ru-ru"
        response = requests.get(url, headers=_SCRAPE_HDR, timeout=_TIMEOUT)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        titles: list[str] = []
        snippets: list[str] = []
        links: list[str] = []
        for result in soup.select(".result")[:5]:
            anchor = result.select_one("a.result__a")
            if anchor:
                title = anchor.get_text(" ", strip=True)
                href = anchor.get("href", "")
                if title:
                    titles.append(title[:70])
                direct = _public_result_url(href, "https://duckduckgo.com")
                if direct and direct not in links:
                    links.append(direct)
            snippet = result.select_one(".result__snippet")
            if snippet:
                text = snippet.get_text(" ", strip=True)
                if text:
                    snippets.append(text[:160])

        if not titles and not snippets:
            return None
        result: dict = {"source": "🦆 DuckDuckGo"}
        if titles:
            result["Найдено на"] = titles[:5]
        if snippets:
            result["Сниппеты"] = snippets[:3]
        if links:
            result["Ссылки"] = list(dict.fromkeys(links))[:4]
        return result
    except Exception:
        log.debug("DuckDuckGo lookup failed", exc_info=True)
        return None


def _src_bing(query: str) -> dict | None:
    """Free public HTML search page; no paid API key required."""
    try:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({
            "q": query, "setlang": "ru", "cc": "ru", "count": 5,
        })
        response = requests.get(url, headers=_SCRAPE_HDR, timeout=_TIMEOUT)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        snippets = []
        links = []
        for item in soup.select("li.b_algo")[:5]:
            node = item.select_one(".b_caption p, p")
            text = node.get_text(" ", strip=True) if node else ""
            if text and len(text) > 25:
                snippets.append(text[:180])
            anchor = item.select_one("h2 a[href]")
            if anchor:
                direct = _public_result_url(anchor.get("href", ""), "https://www.bing.com")
                if direct and direct not in links:
                    links.append(direct)
        if not snippets and not links:
            return None
        return {"source": "bing", "Дополнительная информация": snippets[:4], "Ссылки": links[:5]}
    except Exception:
        log.debug("Bing lookup failed", exc_info=True)
        return None


def _src_yandex(query: str) -> dict | None:
    """Free public Russian search page; no paid API key required."""
    try:
        url = "https://yandex.ru/search/?" + urllib.parse.urlencode({"text": query, "lr": "225"})
        response = requests.get(url, headers=_SCRAPE_HDR, timeout=_TIMEOUT)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        snippets = []
        links = []
        selectors = (
            ".OrganicTextContentSpan", ".TextContainer", ".organic__text",
            ".serp-item__text", ".Organic-ContentWrapper",
        )
        for selector in selectors:
            for node in soup.select(selector):
                text = node.get_text(" ", strip=True)
                if len(text) > 35 and text not in snippets:
                    snippets.append(text[:180])
            if len(snippets) >= 4:
                break
        for anchor in soup.select(".serp-item a[href], a.OrganicTitle-Link[href]"):
            direct = _public_result_url(anchor.get("href", ""), "https://yandex.ru")
            if direct and direct not in links:
                links.append(direct)
        if not snippets and not links:
            return None
        return {"source": "yandex", "Дополнительная информация": snippets[:4], "Ссылки": links[:5]}
    except Exception:
        log.debug("Yandex lookup failed", exc_info=True)
        return None


def _src_maigret(username: str) -> dict | None:
    """Search public username profiles with the free Maigret database."""
    try:
        import asyncio
        import logging as std_logging
        from importlib.resources import files
        from maigret import MaigretDatabase, search as maigret_search
    except ImportError:
        return {
            "source": "maigret",
            "Примечание": (
                "Полный поиск профилей недоступен в этой установке: Maigret не установлен. "
                "В Pydroid его зависимости могут не собраться; запустите бота в Termux "
                "или Linux и установите там maigret."
            ),
        }

    try:
        db_path = files("maigret").joinpath("resources", "data.json")
        database = MaigretDatabase().load_from_path(str(db_path))
        # No top limit: check every enabled username site in the current Maigret DB.
        sites = database.ranked_sites_dict(disabled=False)
        if not sites:
            return None

        # Maigret is an async library. The bot handler runs in a worker thread,
        # so asyncio.run is safe here and keeps the Telegram polling loop free.
        results = asyncio.run(maigret_search(
            username=username,
            site_dict=sites,
            logger=std_logging.getLogger("maigret"),
            timeout=5,
            is_parsing_enabled=True,
            max_connections=15,
            no_progressbar=True,
            retries=0,
        ))

        found_profiles: list[str] = []
        extra_links: list[str] = []
        fields: OrderedDict[str, list[str]] = OrderedDict()
        field_map = {
            "fullname": "fullname", "username": "username", "uid": "uid",
            "bio": "bio", "location": "location", "city": "city",
            "country": "country", "birthday": "birthday", "created_at": "created_at",
            "updated_at": "updated_at", "latest_activity_at": "latest_activity_at",
            "follower_count": "follower_count", "following_count": "following_count",
            "posts_count": "posts_count", "occupation": "occupation", "company": "company",
            "interests": "interests", "email": "email", "gender": "gender",
            "is_verified": "is_verified", "website": "website",
        }
        for result in results.values():
            status = result.get("status")
            if not status or not status.is_found():
                continue

            profile_url = _public_result_url(str(result.get("url_user", "")))
            if profile_url and profile_url not in found_profiles:
                found_profiles.append(profile_url)

            ids_data = result.get("ids_data") or {}
            if not isinstance(ids_data, dict):
                continue
            for key, value in ids_data.items():
                if value is None or value == "":
                    continue
                if key in field_map:
                    values = value if isinstance(value, (list, tuple, set)) else [value]
                    for item in values:
                        text = re.sub(r"\s+", " ", str(item)).strip()
                        if text and text not in fields.setdefault(field_map[key], []):
                            fields[field_map[key]].append(text[:250])
                if key in ("links", "social_links", "website", "blog_url"):
                    for candidate in re.findall(r"https?://[^\s\]<>\"']+", str(value)):
                        candidate = candidate.rstrip(".,;:)}`")
                        direct = _public_result_url(candidate)
                        if direct and direct not in extra_links:
                            extra_links.append(direct)

        links = list(dict.fromkeys(found_profiles + extra_links))[:60]
        if not links and not fields:
            return None
        answer: dict = {
            "source": "maigret",
            "Сайтов проверено": len(sites),
            "Количество найденных профилей": len(found_profiles),
            "Ссылки": links,
        }
        answer.update(fields)
        return answer
    except Exception as exc:
        log.warning("Maigret username search failed: %s", exc)
        return None


# ── formatter ─────────────────────────────────────────────────────────────────
def _fmt_osint(results: list[dict], query: str) -> str:
    # Keep useful facts, hide provider names and raw result titles, but preserve
    # direct profile/page links returned by public search results.
    label_map = {
        "Имя": "Имя", "ID": "Идентификатор", "Тип": "Тип",
        "Username": "Имя пользователя", "Bio": "Описание",
        "Оператор": "Оператор", "Регион": "Регион", "E.164": "Международный формат",
        "Валиден": "Проверка номера", "Статус": "Статус номера",
        "Определён как": "Определение номера", "Оценка": "Оценка",
        "Комментарии": "Отзывы и комментарии", "Категория": "Категория",
        "Активность": "Количество упоминаний", "Описание": "Описание",
        "Найдено": "Дополнительная информация", "Отзывы": "Отзывы и комментарии",
        "Пометка": "Пометка", "Сниппеты": "Дополнительная информация",
        "Дополнительная информация": "Дополнительная информация",
        "Примечание": "Расширенный поиск",
        "Сайтов проверено": "Проверено сайтов",
        "Количество найденных профилей": "Найдено профилей",
        "fullname": "Имя", "username": "Имя пользователя", "uid": "ID профиля",
        "bio": "Описание профиля", "location": "Местоположение", "city": "Город",
        "country": "Страна", "birthday": "Дата рождения", "created_at": "Дата регистрации",
        "updated_at": "Обновлён профиль", "latest_activity_at": "Последняя активность",
        "follower_count": "Подписчики", "following_count": "Подписки",
        "posts_count": "Публикации", "occupation": "Профессия", "company": "Организация",
        "interests": "Интересы", "email": "Публичная почта", "gender": "Пол",
        "is_verified": "Подтверждённый профиль", "website": "Сайт",
    }
    merged: OrderedDict[str, list[str]] = OrderedDict()
    seen: set[tuple[str, str]] = set()
    result_links: list[str] = []
    for info in results:
        if not info:
            continue
        for key, value in info.items():
            if key == "Ссылки":
                for raw_url in value if isinstance(value, list) else [value]:
                    direct = _public_result_url(str(raw_url))
                    if direct and direct not in result_links:
                        result_links.append(direct)
                continue
            if key in ("source", "error", "Найдено на"):
                continue
            label = label_map.get(key, key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = re.sub(r"\s+", " ", str(item)).strip()
                if not text:
                    continue
                marker = (label.casefold(), text.casefold())
                if marker in seen:
                    continue
                seen.add(marker)
                merged.setdefault(label, []).append(text[:250])

    safe_query = html.escape(query, quote=False)
    if not merged and not result_links:
        return f"❌ Информации по запросу <code>{safe_query}</code> не найдено."

    lines = [f"🔎 <b>Информация:</b> <code>{safe_query}</code>\n"]
    for label, values in merged.items():
        lines.append(f"<b>{html.escape(label, quote=False)}:</b>")
        lines.extend(f"• {html.escape(value, quote=False)}" for value in values)
        lines.append("")

    if result_links:
        lines.append("<b>Ссылки на профили и страницы:</b>")
        for url in result_links[:60]:
            parsed = urllib.parse.urlparse(url)
            shown = (parsed.hostname or "") + parsed.path
            shown = shown.rstrip("/") or (parsed.hostname or "Открыть страницу")
            if len(shown) > 70:
                shown = shown[:67] + "…"
            lines.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(shown, quote=False)}</a>')
        lines.append("")

    if any("Количество найденных профилей" in info for info in results if info):
        lines.append("<i>Профили найдены по совпадению имени пользователя; совпадение не подтверждает, что они принадлежат одному человеку.</i>")

    return "\n".join(lines)


def _split_telegram_html(text: str, limit: int = 3900) -> list[str]:
    """Split a formatted answer at line boundaries, keeping HTML tags intact."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
            extra = len(line)
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


# ── .info handler ─────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: bool(m.text and re.match(r"^\.info(?:@\w+)?(?:\s|$)", m.text.strip(), re.I)))
def cmd_info(msg: telebot.types.Message):
    command = re.match(r"^\.info(?:@\w+)?(?:\s+(.*))?$", msg.text.strip(), re.I | re.S)
    raw = (command.group(1) or "").strip() if command else ""
    if not raw:
        bot.reply_to(
            msg,
            "\u26a0\ufe0f <b>\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435:</b>\n"
            "  <code>.info @username</code> \u2014 \u043f\u043e\u0438\u0441\u043a \u043f\u043e \u043d\u0438\u043a\u0443\n"
            "  <code>.info https://t.me/username</code> \u2014 \u0441\u0441\u044b\u043b\u043a\u0430 \u043d\u0430 \u043f\u0440\u043e\u0444\u0438\u043b\u044c\n"
            "  <code>.info +79001234567</code> \u2014 \u043f\u043e\u0438\u0441\u043a \u043f\u043e \u043d\u043e\u043c\u0435\u0440\u0443",
        )
        return

    if len(raw) > 128:
        bot.reply_to(msg, "\u26a0\ufe0f \u0417\u0430\u043f\u0440\u043e\u0441 \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0434\u043b\u0438\u043d\u043d\u044b\u0439. \u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c \u2014 128 \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432.")
        return

    phone    = _normalize_phone(raw)
    username = _normalize_username(raw)

    # Отвечаем сразу — освобождаем воркер-поток бота.
    # Вся тяжёлая работа идёт в daemon-поток: бот остаётся отзывчивым.
    loading = bot.reply_to(msg, "\u23f3 \u0421\u043e\u0431\u0438\u0440\u0430\u044e \u0434\u0430\u043d\u043d\u044b\u0435, \u043f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435\u2026")
    threading.Thread(
        target=_do_info,
        args=(msg, loading, raw, phone, username),
        daemon=True,
    ).start()


def _do_info(msg, loading_msg, raw: str, phone, username):
    """Тяжёлая работа .info — выполняется в daemon-потоке вне воркер-пула бота."""

    def run_parallel(tasks):
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = [pool.submit(task) for task in tasks]
            completed = []
            for future in futures:
                try:
                    value = future.result(timeout=45)
                    if value:
                        completed.append(value)
                except Exception as exc:
                    log.warning(".info source failed: %s", exc)
            return completed

    results: list[dict] = []

    if phone:
        web_query = f'"{phone}" номер отзывы информация'
        phone_tasks = [
            lambda p=phone: _src_phonenumbers_local(p),
            lambda q=web_query: _src_google(q),
            lambda q=web_query: _src_duckduckgo(q),
            lambda q=web_query: _src_bing(q),
            lambda q=web_query: _src_yandex(q),
        ]
        if phone.startswith("+7"):
            phone_tasks[1:1] = [
                lambda p=phone: _src_nomerorg(p),
                lambda p=phone: _src_zvonili(p),
                lambda p=phone: _src_kto_zvonit(p),
            ]
        results = run_parallel(phone_tasks)

    elif username:
        query = f'"{username}" Telegram профиль информация'
        results = run_parallel([
            lambda u=username: _src_tg_username(u),
            lambda u=username: _src_maigret(u),
            lambda q=query: _src_google(q),
            lambda q=query: _src_duckduckgo(q),
            lambda q=query: _src_bing(q),
            lambda q=query: _src_yandex(q),
        ])

    else:
        try:
            bot.edit_message_text(
                "\u26a0\ufe0f \u0424\u043e\u0440\u043c\u0430\u0442 \u043d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d.\n"
                "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 <code>@username</code> \u0438\u043b\u0438 <code>+79001234567</code>",
                loading_msg.chat.id,
                loading_msg.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    answer_chunks = _split_telegram_html(_fmt_osint(results, raw))
    for index, chunk in enumerate(answer_chunks):
        try:
            if index == 0:
                bot.edit_message_text(
                    chunk,
                    loading_msg.chat.id,
                    loading_msg.message_id,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            else:
                bot.send_message(
                    msg.chat.id,
                    chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            log.error(f"send result chunk: {e}")


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
    bot.edit_message_text(
        "⚙️ <b>Настройки уведомлений</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "toggle_delete")
def cb_toggle_delete(call):
    s = get_settings(call.from_user.id)
    s["delete"] = not s["delete"]
    state = "включены 🟢" if s["delete"] else "выключены 🔴"
    bot.answer_callback_query(call.id, f"Уведомления об удалении {state}")
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data == "toggle_edit")
def cb_toggle_edit(call):
    s = get_settings(call.from_user.id)
    s["edit"] = not s["edit"]
    state = "включены 🟢" if s["edit"] else "выключены 🔴"
    bot.answer_callback_query(call.id, f"Уведомления об изменении {state}")
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb_settings(call.from_user.id)
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
    old      = cache_get(bc_id, msg.chat.id, msg.message_id)
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
        data = cache_pop(bc_id, chat_id, msg_id)

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
