"""Pure validation and Telegram keyboard construction."""
import math
import re
from urllib.parse import urlsplit
from urllib.parse import quote, quote_plus

WELCOME = """👋 Привет!
Я помогу найти музыку 🎶, отправь мне что-то из этого:

🎵 Название песни или исполнителя
🔤 Слова из песни
🎙 Голосовое сообщение с музыкой
📹 Видео с музыкой
🔊 Аудиозапись
🎥 Видеосообщение с музыкой
🔗 Ссылку на видео в Instagram, Tik-Tok, YouTube и другие сайты

🕺 Наслаждайся!"""
PAGE_SIZE = 12
MAX_PAGES = 10
MAX_INPUT = 20 * 1024 * 1024
MAX_OUTPUT = 49 * 1024 * 1024
DOMAINS = {"youtube.com", "youtu.be", "instagram.com", "tiktok.com",
           "soundcloud.com", "vimeo.com", "dailymotion.com", "dai.ly", "vk.com", "vkvideo.ru"}


def media_url(text):
    parts = urlsplit(text.strip())
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)
            or not any(host == d or host.endswith("." + d) for d in DOMAINS)):
        raise ValueError("Пришли HTTPS-ссылку на YouTube, Instagram, TikTok, VK, SoundCloud, Vimeo или Dailymotion.")
    return text.strip()


def normalize(entries):
    tracks, seen = [], set()
    for item in entries:
        video_id = str(item.get("videoId") or item.get("id") or '')
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
            continue
        if item.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            continue
        seen.add(video_id)
        tracks.append({"id": video_id, "title": str(item.get("track") or item.get("title") or "Без названия")[:200],
                       "artist": str(item.get("artist") or ' & '.join(a.get('name','') for a in (item.get('artists') or []) if a.get('name')) or item.get("uploader") or item.get("channel") or "")[:200],
                       "duration": item.get("duration_seconds") if 'duration_seconds' in item else item.get("duration")})
    return tracks


def label(track):
    duration = track.get("duration")
    prefix = ""
    if isinstance(duration, (float, int)) and math.isfinite(duration) and duration >= 0:
        seconds = int(duration)
        prefix = f"• {seconds // 60}:{seconds % 60:02d} • "
    title = " ".join(track["title"].split())
    if track.get("artist"):
        title += " — " + " ".join(track["artist"].split())
    result = prefix + title
    return result[:200]


def keyboard(key, tracks, page, has_more):
    start = page * PAGE_SIZE
    rows = [[{"text": label(track), "callback_data": f"play:{key}:{i}"}]
            for i, track in enumerate(tracks[start:start + PAGE_SIZE], start)]
    if has_more and page + 1 < MAX_PAGES:
        rows.append([{"text": "➕ Больше треков", "style": "success",
                      "callback_data": f"more:{key}:{page + 1}"}])
    rows.append([{"text": "🔍", "switch_inline_query_current_chat": ""}])
    return {"inline_keyboard": rows}


def audio_keyboard(video_id, lyrics=True):
    rows = []
    if lyrics:
        rows.append([{"text": "🔤 Текст песни", "callback_data": f"lyrics:{video_id}:0"}])
    rows.append([{"text": "🔍", "switch_inline_query_current_chat": ""}])
    return {"inline_keyboard": rows}


def recognition_keyboard(key, title, artist, wrong=True):
    query = f"{title} — {artist}"
    rows = [[{"text": "Google", "url": "https://www.google.com/search?q=" + quote_plus(query)},
             {"text": "YouTube Music", "url": "https://music.youtube.com/search?q=" + quote_plus(query)},
             {"text": "Spotify", "url": "https://open.spotify.com/search/" + quote(query, safe="")}],
            [{"text": "🔍 Поиск по исполнителю", "switch_inline_query_current_chat": artist}]]
    if wrong:
        rows.append([{"text": "❌ Неправильная песня ❌", "callback_data": f"wrong:{key}:0"}])
    return {"inline_keyboard": rows}
