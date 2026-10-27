"""Сайт ts-speeds: API для скачивания видео и оценки эдитов. На Vercel запускается как функция."""
import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

import anthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import downloader
import rater

log = logging.getLogger("app")
app = FastAPI(title="ts-speeds")

PUBLIC = Path(__file__).parent / "public"
CHUNK = 1024 * 1024


class DownloadRequest(BaseModel):
    url: str


@app.get("/api/health")
def health():
    return {"ok": True, "rating": bool(os.getenv("ANTHROPIC_API_KEY")),
            "code_required": bool(os.getenv("ACCESS_CODE"))}


@app.post("/api/download")
async def download(req: DownloadRequest):
    url = downloader.find_url(req.url)
    if not url:
        raise HTTPException(400, "Нужна ссылка на TikTok, YouTube или Instagram.")
    try:
        path, title = await asyncio.to_thread(downloader.download, url)
    except downloader.DownloadError as e:
        raise HTTPException(422, str(e))

    def stream():
        # Отдаём кусками: потоковый ответ на Vercel не упирается в лимит 4,5 МБ
        try:
            with open(path, "rb") as f:
                while chunk := f.read(CHUNK):
                    yield chunk
        finally:
            downloader.cleanup(path)

    name = re.sub(r'[\\/:*?"<>|\n\r]+', " ", title).strip()[:80] or "video"
    return StreamingResponse(stream(), media_type="video/mp4", headers={
        "Content-Length": str(path.stat().st_size),
        "Content-Disposition": f"attachment; filename=\"video.mp4\"; filename*=UTF-8''{quote(name)}.mp4",
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


# На Vercel папку public раздаёт CDN (монтировать её там нельзя), а локально — FastAPI
if not os.getenv("VERCEL"):
    @app.get("/")
    def index():
        return FileResponse(PUBLIC / "index.html")

    app.mount("/", StaticFiles(directory=PUBLIC), name="public")
