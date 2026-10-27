"""Telegram-бот: скачивает видео без водяного знака и оценивает эдиты."""
import asyncio
import logging
import os
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv

load_dotenv()

import downloader  # noqa: E402  (после load_dotenv, чтобы модули видели переменные)
import rater  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

# Telegram отдаёт ботам на скачивание файлы только до 20 МБ
MAX_INCOMING = 20 * 1024 * 1024
# Сколько тяжёлых задач (загрузка/оценка) выполняется одновременно
jobs = asyncio.Semaphore(int(os.getenv("MAX_JOBS", "3")))

dp = Dispatcher()

HELP = (
    "👋 <b>Привет!</b> Я умею:\n\n"
    "📥 <b>Скачивать видео</b> — просто пришли ссылку на TikTok, YouTube (Shorts тоже) "
    "или Instagram Reels. TikTok — без водяного знака, в лучшем доступном качестве.\n\n"
    "⭐ <b>Оценивать эдиты</b> — пришли сам видеофайл (до 20 МБ), и я поставлю оценку "
    "от 1 до 10 с разбором и советами.\n"
    "Или: <code>/rate ссылка</code> — скачаю и оценю.\n\n"
    "⚠️ Звук я не слышу — оцениваю картинку: цвет, переходы, эффекты, монтаж, качество."
)


@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP)


async def _rate_and_reply(message: Message, path: Path) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        await message.reply("⭐ Оценка эдитов пока не подключена (на сервере не задан ANTHROPIC_API_KEY).")
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        rating = await rater.rate_video(path)
    except rater.RateError as e:
        await message.reply(f"😕 Не смог оценить: {e}")
        return
    except Exception:
        log.exception("rating failed")
        await message.reply("😕 Что-то пошло не так при оценке, попробуй ещё раз позже.")
        return
    await message.reply(rater.format_rating(rating))


@dp.message(Command("rate"))
async def cmd_rate(message: Message) -> None:
    url = downloader.find_url(message.text) or (
        message.reply_to_message and downloader.find_url(message.reply_to_message.text)
    )
    if not url:
        await message.reply("Пришли так: <code>/rate ссылка</code> — или просто отправь видеофайл.")
        return
    status = await message.reply("⏳ Скачиваю и смотрю эдит…")
    async with jobs:
        try:
            path, _ = await downloader.download(url)
        except downloader.DownloadError as e:
            await status.edit_text(f"❌ Не удалось скачать: {_short(e)}")
            return
        try:
            await _rate_and_reply(message, path)
        finally:
            path.unlink(missing_ok=True)
    await status.delete()


@dp.message(F.video | F.document.mime_type.startswith("video/") | F.video_note | F.animation)
async def on_video(message: Message) -> None:
    media = message.video or message.document or message.video_note or message.animation
    if media.file_size and media.file_size > MAX_INCOMING:
        await message.reply(
            "Файл больше 20 МБ — Telegram не даёт ботам его скачать. "
            "Сожми видео или залей в TikTok и пришли <code>/rate ссылка</code>."
        )
        return
    status = await message.reply("👀 Смотрю эдит…")
    downloader.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = downloader.DOWNLOAD_DIR / f"in_{uuid.uuid4().hex}.mp4"
    async with jobs:
        try:
            await message.bot.download(media, destination=path)
            await _rate_and_reply(message, path)
        finally:
            path.unlink(missing_ok=True)
    await status.delete()


@dp.message(F.text)
async def on_text(message: Message) -> None:
    url = downloader.find_url(message.text)
    if not url:
        await message.answer("Пришли ссылку на TikTok / YouTube / Instagram или видео для оценки. /help")
        return
    status = await message.reply("⏳ Скачиваю…")
    async with jobs:
        try:
            path, title = await downloader.download(url)
        except downloader.DownloadError as e:
            await status.edit_text(f"❌ Не удалось скачать: {_short(e)}")
            return
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            await message.reply_video(
                FSInputFile(path, filename=f"{title[:60]}.mp4"),
                supports_streaming=True,
            )
        except Exception:
            log.exception("send failed")
            await status.edit_text("❌ Не получилось отправить видео в Telegram.")
            return
        finally:
            path.unlink(missing_ok=True)
    await status.delete()


def _short(e: Exception) -> str:
    text = str(e).replace("ERROR: ", "")
    if "login" in text.lower() or "sign in" in text.lower() or "cookies" in text.lower():
        return "площадка требует авторизацию (нужен cookies.txt на сервере)."
    if "private" in text.lower():
        return "видео приватное."
    return text[:200]


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
