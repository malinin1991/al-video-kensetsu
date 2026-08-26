FROM python:3.14-slim

ARG BUILD_TIME=unknown
ARG GIT_SHA=unknown
ARG IMAGE_SOURCE=

LABEL org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}"

# Системные пакеты:
# curl — healthcheck; ca-certificates — HTTPS для runtime-скачивания FFmpeg;
# mkvtoolnix — CLI (mkvmerge), без GUI
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
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