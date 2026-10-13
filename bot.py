"""Telegram music search and recognition bot. Python 3.11+."""
import asyncio
from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
import re
from html import escape
from urllib.parse import quote_plus
from urllib.parse import urlsplit
import aiohttp
from ytmusicapi import YTMusic
import requests

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message, CallbackQuery, InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedAudio, InputTextMessageContent, ChosenInlineResult, InputMediaAudio
from shazamio import Shazam

from core import (WELCOME, PAGE_SIZE, MAX_PAGES, MAX_INPUT, MAX_OUTPUT,
                  keyboard, media_url, normalize, audio_keyboard, recognition_keyboard)
from store import Store
import admin

dp = Router()
db = None
username = ''
sessions = OrderedDict()
active = set()
running = {}
workers = asyncio.Semaphore(3)
SESSION_TTL = 3600
log = logging.getLogger("music_bot")


def load_env():
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                name, sep, value = line.partition("=")
                if sep:
                    os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


async def command(*args, timeout=120):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        # Provider output can contain private URLs; never echo it to chat or logs.
        raise RuntimeError("media_service_failed")
    return stdout.decode("utf-8", errors="replace")


async def ytdlp(*args):
    return await command(sys.executable, "-m", "yt_dlp", "--ignore-config",
                         "--no-cache-dir", "--no-plugin-dirs", "--socket-timeout", "15",
                         "--retries", "1", "--fragment-retries", "1", *args, timeout=180)


async def search(query, count, songs=False):
    def fetch():
        with requests.Session() as session:
            from functools import partial
            session.request = partial(session.request,timeout=20)
            data = YTMusic(requests_session=session).search(query,filter='songs' if songs else None,limit=count)
            return normalize(data)
    return await asyncio.to_thread(fetch)


async def download(url, directory):
    await ytdlp("--no-playlist", "--max-filesize", "49M", "--match-filters",
               "!is_live & duration <= 1200", "-f", "bestaudio/best",
               "-x", "--audio-format", "mp3", "--audio-quality", "128K",
               "-o", str(Path(directory) / "audio.%(ext)s"), "--", media_url(url))
    path = Path(directory) / "audio.mp3"
    if not path.exists() or not 0 < path.stat().st_size <= MAX_OUTPUT:
        raise ValueError("Не удалось получить аудио до 49 МБ. Попробуй другой трек или отправь короткий фрагмент.")
    return path


async def download_video(url, directory):
    await ytdlp('--no-playlist', '--max-filesize', '49M', '--match-filters', '!is_live & duration <= 1200',
               '-f', 'bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4]/best',
               '--merge-output-format', 'mp4', '--remux-video', 'mp4',
               '-o', str(Path(directory) / 'video.%(ext)s'), '--', media_url(url))
    path = Path(directory) / 'video.mp4'
    if not path.exists() or not 0 < path.stat().st_size <= MAX_OUTPUT:
        raise ValueError('Не удалось скачать видео до 49 МБ.')
    return path


def remember(query, tracks, owner, chat):
    key = secrets.token_hex(6)
    sessions[key] = {'query': query, 'tracks': tracks, 'owner': owner,
                     'created': time.monotonic(), 'chat': chat}
    prune_sessions()
    return key


async def send_audio(message, track):
    video_id = track['id']
    record = db.media('audio:' + video_id)
    caption = f'<a href="https://t.me/{username}?start=music_yt_{video_id}">@{username}</a> | <a href="https://song.link/y/{video_id}">info</a>'
    kwargs = {'title': track['title'][:128], 'performer': track.get('artist', '')[:128] or None,
              'caption': caption, 'parse_mode': 'HTML', 'reply_markup': audio_keyboard(video_id)}
    if record:
        return await message.answer_audio(record['file_id'], **kwargs)
    with tempfile.TemporaryDirectory(prefix='music-bot-') as directory:
        path = await download(f'https://www.youtube.com/watch?v={video_id}', directory)
        sent = await message.answer_audio(FSInputFile(path), **kwargs)
        db.save_media('audio:' + video_id, {**track, 'file_id': sent.audio.file_id})
        return sent


async def send_video(message, url):
    key = secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix='music-bot-') as directory:
        path = await download_video(url, directory)
        caption = f'<a href="{escape(url, quote=True)}">\u200b\u200b</a><a href="https://t.me/{username}?start=video_id_{key}">🎧 Shazam</a> | <a href="{escape(url, quote=True)}">source</a>'
        sent = await message.answer_video(FSInputFile(path), caption=caption, parse_mode='HTML', supports_streaming=True,
            reply_markup={'inline_keyboard': [[{'text':'🎵 Распознать музыку 🎵','callback_data':f'recognize:{key}:0'}],
                                               [{'text':'🔍','switch_inline_query_current_chat':''}]]})
        db.save_media('video:' + key, {'url':url, 'file_id':sent.video.file_id})
        # shortcut: automatic recognition observed on TikTok; other sites keep the manual button.
        if 'tiktok.com' in (urlsplit(url).hostname or ''):
            try:
                track = await recognize(str(path),directory)
                if track:
                    await recognized(sent,track,message.from_user.id,key)
            except Exception as error:
                log.warning('Automatic recognition failed: %s',type(error).__name__)


async def recognized(message, track, owner, key=None):
    title, artist = track.get('title', ''), track.get('subtitle', '')
    key = key or secrets.token_hex(12)
    db.save_media('match:' + key, {'title':title, 'artist':artist})
    caption = f'<code>{escape(title)} — {escape(artist)}</code>\n\n@{username}'
    if message.video:
        video = db.media('video:' + key) or {}
        if video.get('url'):
            url = escape(video['url'], quote=True)
            caption = f'<code>{escape(title)} — {escape(artist)}</code>\n<a href="https://t.me/{username}?start=video_id_{key}">🎧 Shazam</a> | <a href="{url}">source</a>'
        await message.edit_caption(caption=caption, parse_mode='HTML', reply_markup=recognition_keyboard(key,title,artist))
    else:
        await message.answer(caption, parse_mode='HTML', reply_markup=recognition_keyboard(key,title,artist))
    results = await search(f'{title} {artist}', PAGE_SIZE)
    if results:
        await send_audio(message, results[0])


async def fetch_lyrics(track):
    def youtube_lyrics():
        with requests.Session() as session:
            from functools import partial
            session.request = partial(session.request,timeout=15)
            service = YTMusic(requests_session=session)
            watch = service.get_watch_playlist(videoId=track['id'])
            lyrics_id = watch.get('lyrics')
            return service.get_lyrics(lyrics_id).get('lyrics') if lyrics_id else None
    try:
        lyrics = await asyncio.to_thread(youtube_lyrics)
        if lyrics:
            return lyrics
    except Exception:
        pass
    params = {'track_name':track['title'], 'artist_name':track.get('artist','')}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={'User-Agent':'MusicBot/1.0'}) as client:
        async with client.get('https://lrclib.net/api/get', params=params) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            data = await response.json()
            return data.get('plainLyrics')


async def recognize(source, directory):
    clip = Path(directory) / "fragment.wav"
    await command("ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", source,
                  "-t", "12", "-vn", "-ac", "1", "-ar", "44100", clip, timeout=30)
    result = await asyncio.wait_for(Shazam().recognize(str(clip)), timeout=45)
    return result.get("track")


def prune_sessions():
    now = time.monotonic()
    for key in list(sessions):
        if now - sessions[key]["created"] > SESSION_TTL:
            sessions.pop(key)
    while len(sessions) > 500:
        sessions.popitem(last=False)


async def show_results(message, query, owner):
    tracks = await search(query, PAGE_SIZE + 1)
    if not tracks:
        await message.answer("Ничего не нашлось 😔 Попробуй другое название или отправь музыку.")
        return
    key = remember(query, tracks, owner, message.chat.id)
    await message.answer("🔍", reply_parameters={"message_id": message.message_id},
                         reply_markup=keyboard(key, tracks, 0, len(tracks) > PAGE_SIZE))


@dp.message(Command("start"))
async def start(message: Message):
    payload = message.text.partition(' ')[2]
    if payload.startswith('music_yt_') and re.fullmatch(r'[A-Za-z0-9_-]{11}', payload[9:]):
        video_id = payload[9:]
        async def operation():
            track = db.media('audio:' + video_id)
            if not track:
                raw = await ytdlp('--skip-download','--dump-single-json','--',f'https://www.youtube.com/watch?v={video_id}')
                track = normalize([json.loads(raw)])[0]
            await send_audio(message, track)
        await execute(message,message.from_user.id,operation)
        return
    if payload.startswith('video_id_'):
        video = db.media('video:' + payload[9:])
        if video:
            await message.answer_video(video['file_id'], reply_markup={'inline_keyboard':[[
                {'text':'🎵 Распознать музыку 🎵','callback_data':f'recognize:{payload[9:]}:0'}]]})
            return
    if payload.startswith('ref_'):
        db.touch(message.chat.id, message.chat.type, message.from_user.id, payload[4:])
    await message.answer(db.get('welcome', WELCOME))
    if admin.allowed(message):
        await message.answer('/admin — доступные команды администратора')


@dp.message(Command("privacy"))
async def privacy(message: Message):
    await message.answer("Текст поиска передаётся YouTube. Из аудио и видео создаётся 12-секундный "
                         "фрагмент для распознавания через ShazamIO. Временные файлы удаляются после "
                         "обработки. Запросы и найденные треки хранятся в памяти до часа. "
                         "Ссылки обрабатываются через yt-dlp на соответствующем сайте. "
                         "Название и исполнитель передаются LRCLIB для поиска текста. "
                         "ID пользователей, реферальные переходы и ID выданных медиа сохраняются в базе бота.")


async def execute(message, owner, operation):
    if owner in active or len(active) >= 6:
        await message.answer("⏳ Уже ищу музыку. Попробуй чуть позже.")
        return
    active.add(owner)
    running[owner] = asyncio.current_task()
    status = None
    try:
        status = await message.answer("🔎 Ищу музыку…")
        async with workers:
            await operation()
    except ValueError as error:
        await message.answer(str(error))
    except Exception as error:
        log.warning("Request failed: %s", type(error).__name__)
        await message.answer("Не удалось обработать запрос 😔 Попробуй ещё раз или отправь другой фрагмент.")
    finally:
        active.discard(owner)
        running.pop(owner,None)
        if status:
            try:
                await status.delete()
            except Exception:
                pass


@dp.message(F.text)
async def text(message: Message):
    query = message.text.strip()
    if query == "🔍":
        await message.answer("Отправь название, слова песни или фрагмент музыки 🎶")
        return
    if not 1 <= len(query) <= 300:
        await message.answer("Отправь запрос длиной до 300 символов.")
        return

    async def operation():
        if query.lower().startswith(("http://", "https://")):
            url = media_url(query)
            await send_video(message, url)
        else:
            # shortcut: lyrics use YouTube text relevance; add a lyrics index for exact matching.
            await show_results(message, query, message.from_user.id)

    await execute(message, message.from_user.id, operation)


@dp.message(F.voice | F.audio | F.video | F.video_note | F.document)
async def media(message: Message):
    attachment = message.voice or message.audio or message.video or message.video_note or message.document
    if message.document and not (message.document.mime_type or "").startswith(("audio/", "video/")):
        await message.answer("Отправь аудио или видео, пожалуйста.")
        return
    if attachment.file_size and attachment.file_size > MAX_INPUT:
        await message.answer("Файл слишком большой. Пришли фрагмент до 20 МБ.")
        return

    async def operation():
        remote = await message.bot.get_file(attachment.file_id)
        if remote.file_size and remote.file_size > MAX_INPUT:
            raise ValueError("Пришли фрагмент до 20 МБ.")
        with tempfile.TemporaryDirectory(prefix="music-bot-") as directory:
            source = Path(directory) / "input"
            await message.bot.download_file(remote.file_path, destination=source, timeout=60)
            if source.stat().st_size > MAX_INPUT:
                raise ValueError("Пришли фрагмент до 20 МБ.")
            track = await recognize(str(source), directory)
            if not track:
                await message.answer("Не узнал музыку 😔 Пришли фрагмент, где её слышно чётче.")
                return
            await recognized(message, track, message.from_user.id)

    await execute(message, message.from_user.id, operation)


@dp.callback_query()
async def callback(call: CallbackQuery):
    prune_sessions()
    special = re.fullmatch(r'(lyrics|recognize|wrong):([A-Za-z0-9_-]{11,24}):0', call.data or '')
    if special and isinstance(call.message, Message):
        action, key = special.groups()
        record = db.media(('audio:' if action == 'lyrics' else 'video:' if action == 'recognize' else 'match:') + key)
        if not record:
            await call.answer('Данные не найдены. Отправь запрос заново.',show_alert=True)
            return
        await call.answer()
        async def operation():
            if action == 'lyrics':
                lyrics = await fetch_lyrics(record)
                heading = f'<code>{escape(record["title"])} — {escape(record.get("artist", ""))}</code>\n\n'
                body = lyrics or 'Для этого трека лирика недоступна.'
                for offset in range(0,len(body),3000):
                    await call.message.answer(heading + escape(body[offset:offset+3000]) + f'\n\n@{username}',parse_mode='HTML')
                await call.message.edit_reply_markup(reply_markup=audio_keyboard(key,lyrics=False))
            elif action == 'recognize':
                with tempfile.TemporaryDirectory(prefix='music-bot-') as directory:
                    source = await download(record['url'],directory)
                    track = await recognize(str(source),directory)
                    if track:
                        await recognized(call.message,track,call.from_user.id,key)
                    else:
                        await call.message.answer('Не узнал музыку 😔 Пришли другой фрагмент.')
            else:
                tracks = await search(f'{record["title"]} {record["artist"]}',PAGE_SIZE)
                session_key = remember(record['title'],tracks,call.from_user.id,call.message.chat.id)
                await call.message.edit_reply_markup(reply_markup=recognition_keyboard(key,record['title'],record['artist'],wrong=False))
                await call.message.answer('Вот всё, что мы нашли',reply_markup=keyboard(session_key,tracks,0,False))
        await execute(call.message,call.from_user.id,operation)
        return
    try:
        action, key, number = (call.data or "").split(":")
        index = int(number)
        session = sessions[key]
        if (action not in {"play", "more"} or index < 0 or not isinstance(call.message, Message)
                or session["owner"] != call.from_user.id or session["chat"] != call.message.chat.id):
            raise ValueError()
        if action == "play" and index >= len(session["tracks"]):
            raise ValueError()
        if action == "more" and not 1 <= index < MAX_PAGES:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        await call.answer("Поиск устарел или принадлежит другому пользователю. Отправь запрос заново.", show_alert=True)
        return
    await call.answer()

    async def operation():
        if action == "more":
            end = index * 10
            found = await search(session['query'],end+1,songs=True)
            page = found[end-10:end]
            if not page:
                await call.message.answer("Больше треков не нашлось.")
                return
            new_key = remember(session['query'],page,call.from_user.id,call.message.chat.id)
            markup = keyboard(new_key,page,0,len(found)>end and index+1<MAX_PAGES)
            for row in markup['inline_keyboard']:
                if row[0].get('callback_data','').startswith('more:'):
                    row[0]['callback_data'] = f'more:{new_key}:{index+1}'
            await call.message.answer('🔍',reply_parameters={'message_id':call.message.message_id},reply_markup=markup)
        else:
            track = session["tracks"][index]
            await send_audio(call.message, track)

    await execute(call.message, call.from_user.id, operation)


@dp.inline_query()
async def inline(query: InlineQuery):
    if not db.get('inline',True) or not 1 <= len(query.query.strip()) <= 300:
        await query.answer([],cache_time=1,is_personal=True)
        return
    if query.from_user.id in active or len(active)>=6:
        await query.answer([],cache_time=1,is_personal=True)
        return
    active.add(query.from_user.id)
    try:
        page = int(query.offset or 0)
        if not 0 <= page < MAX_PAGES:
            raise ValueError()
        async with workers:
            tracks = await search(query.query,(page+1)*12+1,songs=page>0)
        results = []
        for track in tracks[page*12:(page+1)*12]:
            video_id = track['id']
            db.save_media('track:' + video_id,track)
            cached = db.media('audio:' + video_id)
            if cached:
                results.append(InlineQueryResultCachedAudio(id=video_id,audio_file_id=cached['file_id'],reply_markup=audio_keyboard(video_id)))
            else:
                link = f'https://t.me/{username}?start=music_yt_{video_id}'
                results.append(InlineQueryResultArticle(id=video_id,title=track['title'],description=track.get('artist',''),
                    input_message_content=InputTextMessageContent(message_text=f'🎵 {track["title"]} — {track.get("artist", "")}\n{link}',link_preview_options={'is_disabled':True}),
                    reply_markup={'inline_keyboard':[[{'text':'🔍','switch_inline_query_current_chat':''}]]}))
        await query.answer(results,cache_time=15,is_personal=True,next_offset=str(page+1) if len(tracks)>(page+1)*12 else '')
    except Exception as error:
        log.warning('Inline search failed: %s',type(error).__name__)
        await query.answer([],cache_time=1,is_personal=True)
    finally:
        active.discard(query.from_user.id)


@dp.chosen_inline_result()
async def inline_chosen(chosen: ChosenInlineResult):
    cache_chat = os.environ.get('INLINE_CACHE_CHAT_ID','')
    if not cache_chat or not chosen.inline_message_id or not re.fullmatch(r'[A-Za-z0-9_-]{11}',chosen.result_id):
        return
    if chosen.from_user.id in active or len(active)>=6:
        return
    track = db.media('track:' + chosen.result_id)
    if not track:
        return
    active.add(chosen.from_user.id)
    try:
        async with workers:
            cached = db.media('audio:' + chosen.result_id)
            if not cached:
                with tempfile.TemporaryDirectory(prefix='music-bot-') as directory:
                    path = await download(f'https://www.youtube.com/watch?v={chosen.result_id}',directory)
                    sent = await chosen.bot.send_audio(int(cache_chat),FSInputFile(path),title=track['title'][:128],performer=track.get('artist','')[:128] or None)
                    cached = {**track,'file_id':sent.audio.file_id}
                    db.save_media('audio:' + chosen.result_id,cached)
            await chosen.bot.edit_message_media(inline_message_id=chosen.inline_message_id,
                media=InputMediaAudio(media=cached['file_id'],title=track['title'][:128],performer=track.get('artist','')[:128] or None),
                reply_markup=audio_keyboard(chosen.result_id))
    except Exception as error:
        log.warning('Inline download failed: %s',type(error).__name__)
    finally:
        active.discard(chosen.from_user.id)


@dp.message()
async def unsupported(message: Message):
    await message.answer("Пришли название, слова песни, аудио, видео или ссылку 🎶")


async def track_chat(handler, event, data):
    if isinstance(event,Message):
        # /start records the first referral before any ordinary registration.
        ref = None
        payload = (event.text or '').partition(' ')[2]
        if payload.startswith('ref_') and (event.text or '').startswith('/start'):
            ref = payload[4:]
        db.touch(event.chat.id,event.chat.type,event.from_user.id if event.from_user else None,ref)
    return await handler(event,data)


async def main():
    global db, username
    load_env()
    token = os.environ.get("BOT_TOKEN", "")
    if not token or token == "PASTE_BOTFATHER_TOKEN_HERE":
        raise SystemExit("Укажи BOT_TOKEN в .env. Получи токен своего бота у @BotFather.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("Установи FFmpeg и добавь ffmpeg/ffprobe в PATH.")
    db = Store(os.environ.get('DB_PATH',str(Path(__file__).with_name('bot.sqlite3'))))
    admin.store = db
    admin.running = running
    admin.admins = {int(value.strip()) for value in os.environ.get('ADMIN_IDS','').split(',') if value.strip()}
    app = Dispatcher()
    app.include_router(admin.router)
    app.include_router(dp)
    app.message.outer_middleware(track_chat)
    async def cleanup():
        while True:
            prune_sessions()
            await asyncio.sleep(30)

    janitor = asyncio.create_task(cleanup())
    try:
        async with Bot(token=token) as bot:
            username = (await bot.get_me()).username
            await app.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"])
    finally:
        janitor.cancel()
        await asyncio.gather(janitor, return_exceptions=True)
        db.db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
