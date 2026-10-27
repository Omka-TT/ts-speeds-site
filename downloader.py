"""Скачивание видео с TikTok / YouTube / Instagram через yt-dlp."""
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import deno
import imageio_ffmpeg
import yt_dlp

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?"
    r"(?:tiktok\.com|youtube\.com|youtu\.be|instagram\.com)/\S+",
    re.IGNORECASE,
)

# Vercel даёт функции максимум 5 минут: длинные и тяжёлые ролики не успеют скачаться
MAX_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "200")) * 1024 * 1024
MAX_MINUTES = int(os.getenv("MAX_MINUTES", "10"))

# На Vercel писать можно только в /tmp
WORK_DIR = Path(tempfile.gettempdir()) / "downloads"


class DownloadError(Exception):
    pass


def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _cookies_file() -> str | None:
    """Cookies берём из переменной окружения COOKIES_TXT (содержимое cookies.txt)."""
    text = os.getenv("COOKIES_TXT")
    if not text:
        return None
    path = Path(tempfile.gettempdir()) / "cookies.txt"
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return str(path)


def _opts(out_tmpl: str) -> dict:
    opts = {
        "outtmpl": out_tmpl,
        # Лучшее качество до 1080p, h264 в mp4, чтобы видео открывалось на любом телефоне.
        # Для TikTok yt-dlp сам выбирает версию без водяного знака.
        "format": (
            "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]"
            "/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b"
        ),
        "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "max_filesize": MAX_BYTES,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <=? {MAX_MINUTES * 60}"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "cachedir": False,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        # YouTube требует JS-движок для расшифровки ссылок на видео
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
        "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
    }
    cookies = _cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def download(url: str) -> tuple[Path, str]:
    """Скачивает видео во временную папку. Возвращает путь к mp4 и название ролика.

    Папку файла вызывающий код удаляет сам (cleanup()).
    """
    job_dir = WORK_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL(_opts(str(job_dir / "video.%(ext)s"))) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        cleanup(job_dir)
        raise DownloadError(_humanize(str(e))) from e
    files = [p for p in job_dir.iterdir() if p.suffix in (".mp4", ".webm", ".mkv", ".mov")]
    if not files:
        cleanup(job_dir)
        raise DownloadError(f"Видео длиннее {MAX_MINUTES} минут или слишком тяжёлое. Попробуй ролик покороче.")
    title = (info or {}).get("title") or "video"
    return files[0], title


def cleanup(path: Path) -> None:
    shutil.rmtree(path if path.is_dir() else path.parent, ignore_errors=True)


def _humanize(text: str) -> str:
    text = text.replace("ERROR: ", "")
    low = text.lower()
    if "sign in" in low or "login" in low or "cookies" in low or "not a bot" in low:
        return "Площадка требует вход в аккаунт. Владельцу сайта нужно добавить cookies (см. README)."
    if "private" in low:
        return "Видео приватное."
    if "unsupported url" in low:
        return "Эта ссылка не поддерживается. Нужна ссылка на TikTok, YouTube или Instagram."
    return text[:300]
