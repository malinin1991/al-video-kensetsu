FROM python:3.15.0-slim-trixie AS python-base

# MKVToolNix читает имена из argv в кодировке locale. Без UTF-8 кириллица
# в путях теряется, даже если Python передал subprocess корректные строки.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# У новых CPython могут ещё отсутствовать готовые wheels. Сборка расширений
# psutil/PyYAML выполняется отдельно; компилятор не попадает в runtime.
FROM python-base AS wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libyaml-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

FROM python-base AS runtime

ARG BUILD_TIME=unknown
ARG GIT_SHA=unknown
ARG IMAGE_SOURCE=

LABEL org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}"

# Системные пакеты:
# curl — healthcheck; ca-certificates — HTTPS для runtime-скачивания FFmpeg;
# mkvtoolnix — CLI (mkvmerge) из официального репо mkvtoolnix.download, без GUI.
# Базовый образ Python 3.15 = Debian trixie; suite репо pin'им явно (trixie).
# Ключ APT кладётся AS-IS (официальная инструкция MKVToolNix, без gpg --dearmor).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libyaml-0-2 \
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
COPY --from=wheels /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && python -c "import yaml; assert yaml.__with_libyaml__, 'Required PyYAML C accelerator is missing'" \
    && rm -rf /wheels

COPY app/ ./app/
COPY presets/ ./presets/
COPY run.py ./
COPY build_number.txt ./
COPY docker/entrypoint.sh /docker-entrypoint.sh

# Начальные каталоги; пустой bin для runtime-FFmpeg (том или bind-mount)
RUN mkdir -p /app/uploads /app/outputs /app/logs /app/settings /app/data /app/ffmpeg/bin \
    && chmod +x /docker-entrypoint.sh \
    && python -m compileall -q /app/app

ENV FFMPEG_DIR=/app/ffmpeg
ENV FFMPEG_THREAD_QUEUE_SIZE=1024
ENV FLASK_APP=app.main
ENV FLASK_ENV=production
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/ffmpeg/bin:${PATH}"

EXPOSE 5000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "run.py"]
