FROM python:3.14-slim

ARG BUILD_TIME=unknown
ARG GIT_SHA=unknown
ARG IMAGE_SOURCE=

LABEL org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}"

# Системные пакеты:
# curl — healthcheck; ca-certificates — HTTPS для runtime-скачивания FFmpeg;
# mkvtoolnix — CLI (mkvmerge) из официального репо mkvtoolnix.download, без GUI.
# Базовый образ python:3.14-slim = Debian trixie; suite репо pin'им явно (trixie).
# Ключ APT кладётся AS-IS (официальная инструкция MKVToolNix, без gpg --dearmor).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL -o /etc/apt/keyrings/gpg-pub-moritzbunkus.gpg \
        https://mkvtoolnix.download/gpg-pub-moritzbunkus.gpg \
    && printf 'deb [signed-by=/etc/apt/keyrings/gpg-pub-moritzbunkus.gpg] https://mkvtoolnix.download/debian/ trixie main\n' \
        > /etc/apt/sources.list.d/mkvtoolnix.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY presets/ ./presets/
COPY run.py ./
COPY build_number.txt ./
COPY docker/entrypoint.sh /docker-entrypoint.sh

# Начальные каталоги; пустой bin для runtime-FFmpeg (том или bind-mount)
RUN mkdir -p /app/uploads /app/outputs /app/logs /app/settings /app/data /app/ffmpeg/bin \
    && chmod +x /docker-entrypoint.sh

ENV FFMPEG_DIR=/app/ffmpeg
ENV FFMPEG_THREAD_QUEUE_SIZE=1024
ENV FLASK_APP=app.main
ENV FLASK_ENV=production
ENV PYTHONPATH=/app
ENV PATH="/app/ffmpeg/bin:${PATH}"

EXPOSE 5000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "run.py"]
