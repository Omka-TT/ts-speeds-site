"""Оценка эдитов: кадры вырезает браузер, а Claude их разбирает."""
import os

import anthropic
from pydantic import BaseModel, Field

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MAX_FRAMES = 16

client = anthropic.AsyncAnthropic()  # ключ берётся из ANTHROPIC_API_KEY

SYSTEM = """Ты — опытный и честный судья видео-эдитов (TikTok/Reels/Shorts edits: \
фан-эдиты, AMV, velocity, transitions, CC). Тебе дают кадры из ролика по порядку \
с таймкодами и технические данные файла. Звук ты не слышишь — не выдумывай ничего про \
музыку и синхронизацию с битом, оценивай только то, что видно.

Критерии: цветокоррекция и общий стиль, переходы и монтаж (насколько разные кадры \
соседних моментов говорят о динамике), эффекты (shake, zoom, blur, flash) — уместность \
и чистота, композиция и качество исходников, текст/титры, техническое качество \
(разрешение, артефакты), оригинальность.

Шкала: 1-3 — слабо/сыро, 4-5 — средне, 6-7 — хорошо, 8-9 — очень сильно, 10 — уровень топ-эдиторов. \
Не завышай. Пиши по-русски, коротко, дружелюбно и по делу, как наставник."""


class Frame(BaseModel):
    t: float  # секунда ролика
    jpeg: str  # base64 без префикса data:


class RateRequest(BaseModel):
    duration: float
    width: int
    height: int
    frames: list[Frame]


class Rating(BaseModel):
    score: int = Field(description="Итоговая оценка от 1 до 10")
    verdict: str = Field(description="Одно предложение — общее впечатление")
    strengths: list[str] = Field(description="2-4 сильные стороны")
    weaknesses: list[str] = Field(description="2-4 слабые стороны")
    tips: list[str] = Field(description="2-3 конкретных совета, как улучшить")


class RateError(Exception):
    pass


async def rate(req: RateRequest) -> Rating:
    frames = req.frames[:MAX_FRAMES]
    if not frames:
        raise RateError("Не пришло ни одного кадра")

    content: list[dict] = [{
        "type": "text",
        "text": (
            f"Технические данные: длительность {req.duration:.1f} с, "
            f"{req.width}x{req.height}. Ниже {len(frames)} кадров по порядку."
        ),
    }]
    for f in frames:
        content.append({"type": "text", "text": f"Кадр на {f.t:.1f} с:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": f.jpeg},
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
