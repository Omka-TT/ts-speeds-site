"""Скачивание видео с TikTok / YouTube / Instagram через yt-dlp."""
import asyncio
import os
import re
import uuid
from pathlib import Path

import yt_dlp

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?"
    r"(?:tiktok\.com|youtube\.com|youtu\.be|instagram\.com)/\S+",
    re.IGNORECASE,
)

# Telegram Bot API не даёт ботам отправлять файлы больше 50 МБ
MAX_BYTES = 49 * 1024 * 1024

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
COOKIES_FILE = os.getenv("COOKIES_FILE")  # cookies.txt помогает с Instagram и YouTube


class DownloadError(Exception):
    pass


def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _opts(out_tmpl: str, fmt: str) -> dict:
    opts = {
        "outtmpl": out_tmpl,
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        # Видео в Telegram должно быть h264/aac в mp4, иначе может не проиграться
        "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
    }
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE
    return opts


# От лучшего качества к худшему. Для TikTok yt-dlp сам выбирает версию без водяного знака
# (у варианта с водяным знаком format_note="watermarked" и пониженный приоритет).
FORMATS = [
    "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
    "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]",
    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
    "bv*[height<=480]+ba/b[height<=480]",
]


def _download_sync(url: str) -> tuple[Path, str]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for fmt in FORMATS:
        job = uuid.uuid4().hex
        out_tmpl = str(DOWNLOAD_DIR / f"{job}.%(ext)s")
        try:
            with yt_dlp.YoutubeDL(_opts(out_tmpl, fmt)) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            continue
        files = sorted(DOWNLOAD_DIR.glob(f"{job}.*"))
        if not files:
            continue
        path = files[0]
        if path.stat().st_size <= MAX_BYTES:
            title = (info or {}).get("title") or "video"
            return path, title
        # Слишком большой — пробуем качество пониже
        path.unlink(missing_ok=True)
    if last_error:
        raise DownloadError(str(last_error))
    raise DownloadError("Видео больше 50 МБ даже в низком качестве")


async def download(url: str) -> tuple[Path, str]:
    """Возвращает путь к mp4 и название ролика."""
    return await asyncio.to_thread(_download_sync, url)
