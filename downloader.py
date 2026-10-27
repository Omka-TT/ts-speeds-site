"""Скачивание видео и звука с TikTok / YouTube / Instagram через yt-dlp."""
import os
import re
import shutil
import subprocess
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

# Качество считаем по короткой стороне кадра: вертикальный TikTok 1080x1920 — это 1080p
QUALITIES = [2160, 1440, 1080, 720, 480, 360]
MODES = ("video", "mute", "audio")

# Vercel даёт функции максимум 5 минут: длинные и тяжёлые ролики не успеют скачаться
MAX_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "200")) * 1024 * 1024
MAX_MINUTES = int(os.getenv("MAX_MINUTES", "10"))

# На Vercel писать можно только в /tmp
WORK_DIR = Path(tempfile.gettempdir()) / "downloads"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class DownloadError(Exception):
    pass


def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = URL_RE.search(text)
    return m.group(0) if m else None


def quality_label(q: int) -> str:
    return {2160: "4K", 1440: "2K"}.get(q, f"{q}p")


def _cookies_file() -> str | None:
    """Cookies берём из переменной окружения COOKIES_TXT (содержимое cookies.txt)."""
    text = os.getenv("COOKIES_TXT")
    if not text:
        return None
    path = Path(tempfile.gettempdir()) / "cookies.txt"
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return str(path)


def _base_opts() -> dict:
    opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "cachedir": False,
        "ffmpeg_location": FFMPEG,
        # YouTube требует JS-движок для расшифровки ссылок на видео
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
    }
    cookies = _cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _extract(url: str, opts: dict, download: bool) -> dict:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download) or {}
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_humanize(str(e))) from e


def probe(url: str) -> dict:
    """Информация о ролике и доступные варианты качества (без скачивания)."""
    info = _extract(url, _base_opts(), download=False)
    sides = [
        min(f["width"], f["height"])
        for f in info.get("formats") or []
        if f.get("vcodec") not in (None, "none") and f.get("width") and f.get("height")
    ]
    best = max(sides) if sides else 0
    qualities = [q for q in QUALITIES if q <= best]
    if best and (not qualities or best > qualities[0]) and best < QUALITIES[0]:
        qualities.insert(0, best)  # например, 1024p у TikTok
    duration = info.get("duration") or 0
    return {
        "kind": "video",
        "platform": info.get("extractor_key", "").replace("Tab", "").lower(),
        "title": info.get("title") or "video",
        "author": info.get("uploader") or info.get("channel") or info.get("creator") or "",
        "thumbnail": info.get("thumbnail"),
        "duration": duration,
        "too_long": bool(duration and duration > MAX_MINUTES * 60),
        "qualities": [{"value": q, "label": quality_label(q)} for q in qualities],
    }


def download(url: str, mode: str = "video", quality: int | None = None,
             title_override: str | None = None) -> tuple[Path, str]:
    """Скачивает во временную папку. Возвращает путь к файлу и имя для сохранения.

    mode: video — видео со звуком, mute — без звука, audio — только звук в MP3.
    Папку файла вызывающий код удаляет сам (cleanup()).
    """
    if mode not in MODES:
        raise DownloadError("Неизвестный режим скачивания")
    job_dir = WORK_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)

    opts = _base_opts() | {
        "outtmpl": str(job_dir / "file.%(ext)s"),
        "max_filesize": MAX_BYTES,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <=? {MAX_MINUTES * 60}"),
    }
    if mode == "audio":
        opts |= {
            "format": "ba/b",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata"},
            ],
        }
    else:
        # res — короткая сторона кадра; среди одинакового качества берём h264/aac,
        # чтобы файл открывался на любом телефоне. Для TikTok yt-dlp сам выбирает
        # версию без водяного знака.
        res = f"res:{quality}" if quality else "res"
        opts |= {
            "format": "bv*+ba/b" if mode == "video" else "bv/bv*",
            "format_sort": [res, "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
            "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
        }

    try:
        info = _extract(url, opts, download=True)
    except DownloadError:
        cleanup(job_dir)
        raise
    files = [p for p in job_dir.iterdir() if p.suffix in (".mp4", ".webm", ".mkv", ".mov", ".mp3")]
    if not files:
        cleanup(job_dir)
        raise DownloadError(
            f"Видео длиннее {MAX_MINUTES} минут или слишком тяжёлое. Выбери качество пониже."
        )
    path = files[0]
    if mode == "mute":
        path = _strip_audio(path)

    title = title_override or info.get("title") or "video"
    name = re.sub(r'[\\/:*?"<>|\n\r]+', " ", title).strip()[:80] or "video"
    return path, f"{name}{path.suffix}"


def _strip_audio(path: Path) -> Path:
    """Убирает звуковую дорожку без перекодирования видео."""
    out = path.with_name("mute.mp4")
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy",
         "-movflags", "+faststart", str(out), "-y"],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.exists():
        raise DownloadError("Не получилось убрать звук из видео")
    path.unlink(missing_ok=True)
    return out


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
        return "Эта ссылка не поддерживается."
    return text[:300]
