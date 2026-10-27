# ts-speeds-bot

Telegram-бот, который:

1. **Скачивает видео** с TikTok (без водяного знака), YouTube / Shorts и Instagram Reels в лучшем качестве, которое влезает в лимит Telegram (50 МБ).
2. **Оценивает эдиты** от 1 до 10: вырезает 12 кадров из ролика и отдаёт их Claude, тот пишет оценку, сильные/слабые стороны и советы.
3. **Работает 24/7** на сервере, а не на ноутбуке.

## Как пользоваться ботом

| Что прислать | Что сделает бот |
|---|---|
| Ссылку на TikTok / YouTube / Instagram | Скачает и пришлёт видео |
| Видеофайл (до 20 МБ) | Оценит эдит |
| `/rate ссылка` | Скачает по ссылке и оценит |

## 1. Что нужно получить

- **BOT_TOKEN**: напиши [@BotFather](https://t.me/BotFather), выполни `/newbot` и скопируй токен.
- **ANTHROPIC_API_KEY**: создай ключ на <https://console.anthropic.com/settings/keys> (нужен пополненный баланс). Одна оценка обходится примерно в 2–4 цента.

## 2. Запуск 24/7 (на сервере)

Ноутбук для этого не подходит: нужен компьютер, который всегда включён. Самый простой и надёжный вариант — недорогой VPS (Hetzner, Aeza, Timeweb и т.п., ~300–500 ₽/мес, Ubuntu, 1 ГБ RAM).

На сервере:

```bash
# 1. Установить Docker
curl -fsSL https://get.docker.com | sh

# 2. Скопировать папку проекта на сервер (с компьютера):
#    scp -r ts-speeds-bot root@IP_СЕРВЕРА:/root/

# 3. Создать .env с токенами
cd /root/ts-speeds-bot
cp .env.example .env
nano .env

# 4. Запустить
docker compose up -d --build

# Логи
docker compose logs -f
```

`restart: always` в `docker-compose.yml` поднимает бота после падения и после перезагрузки сервера. Чтобы обновить yt-dlp (если какая-то площадка перестала скачиваться), достаточно `docker compose restart`: он обновляется при каждом старте.

**Без VPS:** подойдёт любой хостинг, который собирает Docker-образ из репозитория (Railway, Render Background Worker, Fly.io, Amvera). Залей проект на GitHub (без `.env`!), создай сервис из репозитория и задай `BOT_TOKEN` и `ANTHROPIC_API_KEY` в переменных окружения. Бот работает через polling, так что открывать порт или настраивать домен не нужно.

## 3. Локальный запуск (для проверки)

Нужны Python 3.12+ и [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) в PATH.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # и вписать токены
python bot.py
```

## Instagram и YouTube: cookies

С серверных IP Instagram почти всегда, а YouTube иногда требуют вход («Sign in to confirm you're not a bot»). Решение:

1. Поставь в браузер расширение **Get cookies.txt LOCALLY** и зайди в Instagram/YouTube под **запасным** аккаунтом.
2. Экспортируй cookies в файл `cookies.txt` и положи его в папку проекта на сервере.
3. Раскомментируй `volumes` в `docker-compose.yml`, добавь в `.env` строку `COOKIES_FILE=/app/cookies.txt` и выполни `docker compose up -d`.

## Ограничения

- Telegram не даёт ботам **отправлять** файлы больше 50 МБ и **скачивать** файлы больше 20 МБ. Длинное YouTube-видео бот попробует скачать в качестве пониже, а если не влезет и так, сообщит об ошибке.
- Звук оценщик не слышит: оценка строится только по картинке (цвет, переходы, эффекты, монтаж, техническое качество).

## Файлы

- [bot.py](bot.py): обработчики Telegram (aiogram 3)
- [downloader.py](downloader.py): скачивание через yt-dlp
- [rater.py](rater.py): кадры через ffmpeg и оценка через Claude
