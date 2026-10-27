"""ts-speed: API для скачивания видео и музыки и оценки эдитов.

На Vercel это одна функция (все запросы /api/* переписываются сюда, см. vercel.json).
Локально: uvicorn api.index:app --reload
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import downloader  # noqa: E402
import rater  # noqa: E402
import spotify  # noqa: E402

log = logging.getLogger("app")
app = FastAPI(title="ts-speed")

PUBLIC = ROOT / "public"
CHUNK = 1024 * 1024


class UrlRequest(BaseModel):
    url: str


class DownloadRequest(UrlRequest):
    mode: str = "video"  # video | mute | audio
    quality: int | None = None  # короткая сторона кадра, None — максимум


def _parse(text: str) -> tuple[str, str]:
    if url := spotify.find_url(text):
        return "spotify", url
    if url := downloader.find_url(text):
        return "video", url
    raise HTTPException(400, "Нужна ссылка на TikTok, YouTube, Instagram или Spotify.")


@app.get("/api/health")
def health():
    return {"ok": True, "rating": bool(os.getenv("ANTHROPIC_API_KEY")),
            "code_required": bool(os.getenv("ACCESS_CODE"))}


@app.post("/api/info")
async def info(req: UrlRequest):
    kind, url = _parse(req.url)
    try:
        if kind == "spotify":
            return await asyncio.to_thread(spotify.probe, url)
        return await asyncio.to_thread(downloader.probe, url)
    except downloader.DownloadError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.exception("info failed")
        raise HTTPException(500, f"Внутренняя ошибка: {type(e).__name__}: {str(e)[:300]}")


@app.post("/api/download")
async def download(req: DownloadRequest):
    kind, url = _parse(req.url)
    try:
        if kind == "spotify":
            path, filename = await asyncio.to_thread(spotify.download, url)
        else:
            path, filename = await asyncio.to_thread(downloader.download, url, req.mode, req.quality)
    except downloader.DownloadError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.exception("download failed")
        raise HTTPException(500, f"Внутренняя ошибка: {type(e).__name__}: {str(e)[:300]}")

    def stream():
        # Отдаём кусками: потоковый ответ на Vercel не упирается в лимит 4,5 МБ
        try:
            with open(path, "rb") as f:
                while chunk := f.read(CHUNK):
                    yield chunk
        finally:
            downloader.cleanup(path)

    media = "audio/mpeg" if path.suffix == ".mp3" else "video/mp4"
    return StreamingResponse(stream(), media_type=media, headers={
        "Content-Length": str(path.stat().st_size),
        "Content-Disposition": f"attachment; filename=\"file{path.suffix}\"; filename*=UTF-8''{quote(filename)}",
    })


@app.post("/api/rate")
async def rate(req: rater.RateRequest, x_access_code: str | None = Header(default=None)):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "Оценка эдитов пока не подключена (не задан ANTHROPIC_API_KEY).")
    code = os.getenv("ACCESS_CODE")
    if code and x_access_code != code:
        raise HTTPException(401, "Неверный код доступа.")
    try:
        return await rater.rate(req)
    except rater.RateError as e:
        raise HTTPException(422, str(e))
    except anthropic.RateLimitError:
        raise HTTPException(429, "Слишком много запросов, попробуй через минуту.")
    except anthropic.APIStatusError as e:
        log.exception("claude error")
        raise HTTPException(502, f"Ошибка ИИ-сервиса ({e.status_code}). Попробуй позже.")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Не удалось связаться с ИИ-сервисом. Попробуй позже.")


# На Vercel папку public раздаёт CDN, а локально — FastAPI
if not os.getenv("VERCEL"):
    @app.get("/")
    def index():
        return FileResponse(PUBLIC / "index.html")

    app.mount("/", StaticFiles(directory=PUBLIC), name="public")
