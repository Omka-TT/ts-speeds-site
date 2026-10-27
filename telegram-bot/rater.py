"""Оценка эдитов: вырезаем кадры через ffmpeg и отдаём их Claude на разбор."""
import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
FRAMES = int(os.getenv("RATE_FRAMES", "12"))

client = anthropic.AsyncAnthropic()  # ключ берётся из ANTHROPIC_API_KEY

SYSTEM = """Ты — опытный и честный судья видео-эдитов (TikTok/Reels/Shorts edits: \
фан-эдиты, AMV, velocity, transitions, CC). Тебе дают кадры из ролика по порядку \
с таймкодами и технические данные файла. Звук ты не слышишь — не выдумывай ничего про \
музыку и синхронизацию с битом, оценивай только то, что видно.

Критерии: цветокоррекция и общий стиль, переходы и монтаж (насколько разные кадры \
соседних моментов говорят о динамике), эффекты (shake, zoom, blur, flash) — уместность \
и чистота, композиция и качество исходников, текст/титры, техническое качество \
(разрешение, артефакты, fps), оригинальность.

Шкала: 1-3 — слабо/сыро, 4-5 — средне, 6-7 — хорошо, 8-9 — очень сильно, 10 — уровень топ-эдиторов. \
Не завышай. Пиши по-русски, коротко, дружелюбно и по делу, как наставник."""


class Rating(BaseModel):
    score: int = Field(description="Итоговая оценка от 1 до 10")
    verdict: str = Field(description="Одно предложение — общее впечатление")
    strengths: list[str] = Field(description="2-4 сильные стороны")
    weaknesses: list[str] = Field(description="2-4 слабые стороны")
    tips: list[str] = Field(description="2-3 конкретных совета, как улучшить")


class RateError(Exception):
    pass


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RateError(f"{args[0]} упал: {err.decode(errors='ignore')[-300:]}")
    return out


async def _probe(path: Path) -> dict:
    out = await _run(
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    )
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RateError("В файле нет видеодорожки")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = round(float(num) / float(den or 1), 1) if float(den or 1) else 0
    return {
        "duration": float(data["format"].get("duration") or 0),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": fps,
        "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
    }


async def _frames(path: Path, duration: float, tmp: Path) -> list[tuple[float, bytes]]:
    n = max(1, FRAMES)
    # Берём кадры равномерно, не трогая самый первый и последний момент
    stamps = [duration * (i + 0.5) / n for i in range(n)] if duration > 0 else [0.0]
    frames = []
    for i, t in enumerate(stamps):
        out = tmp / f"f{i:02d}.jpg"
        await _run(
            "ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale='min(768,iw)':-2", "-q:v", "4", str(out), "-y",
        )
        if out.exists():
            frames.append((t, out.read_bytes()))
    if not frames:
        raise RateError("Не получилось вытащить кадры из видео")
    return frames


async def rate_video(path: Path) -> Rating:
    meta = await _probe(path)
    tmp = Path(tempfile.mkdtemp(prefix="frames_"))
    try:
        frames = await _frames(path, meta["duration"], tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    content: list[dict] = [{
        "type": "text",
        "text": (
            f"Технические данные: длительность {meta['duration']:.1f} с, "
            f"{meta['width']}x{meta['height']}, {meta['fps']} fps, "
            f"звук: {'есть' if meta['has_audio'] else 'нет'}. "
            f"Ниже {len(frames)} кадров по порядку."
        ),
    }]
    for t, jpg in frames:
        content.append({"type": "text", "text": f"Кадр на {t:.1f} с:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(jpg).decode(),
            },
        })
    content.append({"type": "text", "text": "Оцени этот эдит."})

    response = await client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=Rating,
        # Если запрос отклонят фильтры безопасности, API сам повторит его на запасной модели
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RateError("Модель отказалась оценивать это видео")
    rating = response.parsed_output
    rating.score = min(10, max(1, rating.score))
    return rating


def format_rating(r: Rating) -> str:
    bar = "★" * r.score + "☆" * (10 - r.score)
    lines = [f"<b>Оценка: {r.score}/10</b>", bar, "", f"<i>{_esc(r.verdict)}</i>", ""]
    lines.append("✅ <b>Сильные стороны</b>")
    lines += [f"• {_esc(s)}" for s in r.strengths]
    lines.append("")
    lines.append("⚠️ <b>Что хромает</b>")
    lines += [f"• {_esc(s)}" for s in r.weaknesses]
    lines.append("")
    lines.append("💡 <b>Советы</b>")
    lines += [f"• {_esc(s)}" for s in r.tips]
    return "\n".join(lines)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
