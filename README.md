# Shazam Telegram Bot

A Telegram bot for finding songs, recognizing music and downloading public audio and video. Send a song title, an artist, a voice message or a video link, and the bot handles the search and media processing.

Built with Python and aiogram, with YouTube Music for search, ShazamIO for recognition and FFmpeg for audio processing.

## Features

- **Music search:** look up tracks by title, artist or a phrase from a song.
- **Audio delivery:** select a result to receive an MP3 with track metadata and a song.link URL.
- **Music recognition:** identify music in voice messages, audio files, videos and video messages.
- **Video downloads:** download a public video and recognize its soundtrack.
- **Lyrics:** retrieve lyrics from YouTube Music, with LRCLIB as a fallback.
- **Inline search:** find tracks from the message field in any chat where inline bots are available.
- **Audio caching:** reuse Telegram file IDs instead of downloading the same track repeatedly.
- **Search pagination:** keep earlier results available while loading more tracks.
- **Admin tools:** manage the welcome message, view statistics, prepare broadcasts and track referral links.

The first search response contains up to 12 tracks. Additional pages contain up to 10 results each.

## Requirements

- Python 3.11 or 3.12
- FFmpeg, with both `ffmpeg` and `ffprobe` available on `PATH`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Internet access to Telegram and the external music services

Python 3.13 and later have not been verified with the pinned ShazamIO version.

## Quick start

Clone the repository:

```sh
git clone https://github.com/SheraInf/shazam-telegram-bot.git
cd shazam-telegram-bot
```

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe bot.py
```

### Linux / macOS

Install FFmpeg using your system's package manager, then:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env before starting the bot.
python bot.py
```

Set `BOT_TOKEN` in `.env`. Keep this file private.

The bot uses long polling. Run one instance per token and remove any existing webhook before starting it.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token; required |
| `ADMIN_IDS` | Comma-separated Telegram user IDs allowed to use admin commands |
| `INLINE_CACHE_CHAT_ID` | Optional storage chat for audio selected through inline search |
| `DB_PATH` | Optional SQLite path; defaults to `bot.sqlite3` next to `bot.py` |

Environment variables take precedence over values in `.env`.

To configure an administrator, start the bot, send `/id`, add your user ID to `ADMIN_IDS`, and restart the process. Admin access is never assigned automatically to the first user.

## Using the bot

Send one of the following:

- A song title or artist name.
- A text phrase to search through YouTube Music.
- A voice message or audio recording containing music.
- A video or video message.
- A supported public HTTPS video link.

Text searches return a list of tracks. Selecting a track sends its audio file, and the lyrics button retrieves the available text.

Video links return a downloaded video with a recognition action. A successful match adds the song title, artist and links to external music searches. TikTok links also attempt automatic recognition.

Supported link domains include YouTube, Instagram, TikTok, VK, SoundCloud, Vimeo and Dailymotion. Availability depends on the source site.

## Inline search

Enable inline mode with `/setinline` in @BotFather. Then type:

```text
@your_bot_username song title
```

Cached tracks can be sent directly as audio.

For uncached tracks to become audio after selection:

1. Enable `/setinlinefeedback` and set it to `100%` in @BotFather.
2. Set `INLINE_CACHE_CHAT_ID` to a storage chat where the bot can send messages.
3. Start the bot in that chat, or grant posting permission if using a channel.

The selected result initially appears as text and is replaced with audio after download. Without a storage chat, uncached results provide a link to retrieve the track in the bot's private conversation.

## Commands

| Command | Action |
| --- | --- |
| `/start` | Show the welcome message or open a shared media link |
| `/cancel` | Cancel the current operation or pending admin input |
| `/id` | Show your user ID and the current chat ID |
| `/privacy` | Explain data processing |
| `/admin` | Open the admin menu |
| `/statistics` | Show known participants and group counts |
| `/set_start_msg` | Update the welcome message |
| `/set_inline` | Enable or disable inline search within the application |
| `/broadcast` | Prepare a message, preview it and confirm delivery |
| `/referral_system_menu` | Create, disable and inspect referral links |

Administrator commands require an ID listed in `ADMIN_IDS`. Broadcasts require a separate confirmation after preview.

## Storage and processing

SQLite stores application settings, user and chat IDs, referral attribution and cached Telegram media IDs. Referral statistics count a user's first registration through an active referral link.

Search sessions are kept in memory for approximately one hour, with a maximum of 500 sessions. Search buttons expire after a restart; cached media and shared media links remain in SQLite.

Temporary media files are removed after processing. Recognition analyzes the first 12 seconds, so trim a recording if the music starts later.

## Docker

```sh
docker build -t music-bot .
docker volume create music-bot-data
docker run --rm --env-file .env \
  -e DB_PATH=/data/bot.sqlite3 \
  -v music-bot-data:/data \
  music-bot
```

The volume preserves the database between container runs. The Docker configuration is included, but the image build has not been verified.

## Tests

```sh
python -m unittest discover -s tests -v
```

The suite contains 25 tests covering URL validation, keyboards, callbacks, caching, admin access, referral attribution, cancellation and inline handlers.

Separate live service checks have covered music search, MP3 and MP4 downloads, lyrics retrieval and Shazam recognition. Complete Telegram workflows, including inline media replacement and broadcasts, still need end-to-end verification in the deployment environment.

## Project structure

```text
bot.py             Message handlers, search, recognition and media delivery
core.py            Validation and keyboard construction
admin.py           Admin commands, broadcasts and referral management
store.py           SQLite persistence
tests/             Unit and handler tests
requirements.txt   Python dependencies
.env.example       Configuration template
Dockerfile         Container setup
```

## Current limits

- Incoming files: up to 20 MB.
- Outgoing audio and video: up to 49 MB.
- Downloaded publications: up to 20 minutes.
- Up to three concurrent operations and six users with active or queued work.
- Private, age-restricted and region-restricted media are unsupported; browser cookies are not used.
- Text phrases use YouTube Music search rather than a dedicated full-text lyrics index.
- Live downloads have been checked on YouTube; other supported sites still need verification.
- Search results may repeat between mixed search and song-only pages.
- Cancelling a request may leave an underlying search thread running until its network timeout.

External services can change their interfaces or restrict requests. Keep the dependencies updated. For public hosting, restrict outbound access to local and infrastructure addresses because media downloaders follow third-party redirects.

Use downloads only for content you are permitted to access and save. Search queries, media and track metadata are processed by the relevant external services; `/privacy` describes this to users.

## Main dependencies

[aiogram](https://docs.aiogram.dev/) · [ytmusicapi](https://ytmusicapi.readthedocs.io/) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [ShazamIO](https://github.com/shazamio/ShazamIO) · [FFmpeg](https://ffmpeg.org/) · [LRCLIB](https://lrclib.net/docs)
