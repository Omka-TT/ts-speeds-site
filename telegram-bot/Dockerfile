FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
ENV PYTHONUNBUFFERED=1 DOWNLOAD_DIR=/tmp/downloads

# При каждом старте обновляем yt-dlp: TikTok/Instagram/YouTube часто ломают старые версии
CMD pip install --no-cache-dir -q -U "yt-dlp[default]" && python bot.py
