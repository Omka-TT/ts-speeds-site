"""Треки Spotify: берём название и исполнителя со страницы трека, а звук ищем на YouTube.

Сам Spotify отдаёт музыку только в зашифрованном виде, поэтому скачать файл
напрямую с него нельзя — так же работают spotDL и похожие сервисы.
"""
import html
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, ID3NoHeaderError

import downloader

URL_RE = re.compile(r"https?://(?:open\.spotify\.com|spotify\.link)/\S+", re.IGNORECASE)
TRACK_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]{22})")
# С «браузерным» User-Agent Spotify отдаёт пустой веб-плеер без мета-тегов
UA = "curl/8.9.1"


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str
    year: str
    cover: str | None
    duration: int


def find_url(text: str | None) -> str | None:
    if not text:
        return None
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _get(url: str, timeout: int = 15) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.geturl(), res.read()


def _meta(page: str, key: str) -> list[str]:
    pattern = rf'<meta[^>]+(?:property|name)="{re.escape(key)}"[^>]+content="([^"]*)"'
    return [html.unescape(v) for v in re.findall(pattern, page)]


def track(url: str) -> Track:
    if "spotify.link" in url:  # короткая ссылка из приложения
        url, _ = _get(url)
    m = TRACK_RE.search(url)
    if not m:
        if re.search(r"/(album|playlist|artist|episode|show)/", url):
            raise downloader.DownloadError("Пока можно скачать только отдельный трек, не альбом и не плейлист.")
        raise downloader.DownloadError("Не похоже на ссылку на трек Spotify.")
    track_id = m.group(1)
    try:
        _, body = _get(f"https://open.spotify.com/track/{track_id}")
    except OSError as e:
        raise downloader.DownloadError("Не удалось открыть трек в Spotify.") from e
    page = body.decode("utf-8", errors="ignore")

    title = next(iter(_meta(page, "og:title")), "")
    if not title:
        raise downloader.DownloadError("Трек не найден в Spotify.")
    artists = _meta(page, "music:musician_description")
    # og:description: «Исполнитель · Альбом · Song · 1987»
    desc = [p.strip() for p in next(iter(_meta(page, "og:description")), "").split("·")]
    artist = ", ".join(artists) or (desc[0] if desc else "")
    album = desc[1] if len(desc) > 1 else ""
    year = next(iter(_meta(page, "music:release_date")), "")[:4]
    duration = int(next(iter(_meta(page, "music:duration")), "0") or 0)
    return Track(track_id, title, artist, album, year,
                 next(iter(_meta(page, "og:image")), None), duration)


def probe(url: str) -> dict:
    t = track(url)
    return {
        "kind": "spotify",
        "platform": "spotify",
        "title": t.title,
        "author": t.artist,
        "thumbnail": t.cover,
        "duration": t.duration,
        "too_long": False,
        "qualities": [],
    }


def _find_on_youtube(t: Track) -> str:
    """Ищет на YouTube запись, длина которой ближе всего к треку в Spotify."""
    query = f"ytsearch5:{t.artist} - {t.title} audio"
    opts = downloader._base_opts() | {"extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        entries = (ydl.extract_info(query, download=False) or {}).get("entries") or []
    entries = [e for e in entries if e.get("id")]
    if not entries:
        raise downloader.DownloadError("Не нашёл этот трек на YouTube.")
    if t.duration:
        entries.sort(key=lambda e: abs((e.get("duration") or 0) - t.duration))
    return f"https://www.youtube.com/watch?v={entries[0]['id']}"


def download(url: str) -> tuple[Path, str]:
    t = track(url)
    try:
        source = _find_on_youtube(t)
    except yt_dlp.utils.DownloadError as e:
        raise downloader.DownloadError(downloader._humanize(str(e))) from e
    name = f"{t.artist} - {t.title}" if t.artist else t.title
    path, filename = downloader.download(source, mode="audio", title_override=name)
    _tag(path, t)
    return path, filename


def _tag(path: Path, t: Track) -> None:
    """Прописывает в MP3 название, исполнителя, альбом и обложку из Spotify."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TIT2")
    tags.delall("TPE1")
    tags.add(TIT2(encoding=3, text=t.title))
    tags.add(TPE1(encoding=3, text=t.artist))
    if t.album:
        tags.add(TALB(encoding=3, text=t.album))
    if t.year:
        tags.add(TDRC(encoding=3, text=t.year))
    if t.cover:
        try:
            _, img = _get(t.cover)
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img))
        except OSError:
            pass  # без обложки трек всё равно нормальный
    tags.save(path)
