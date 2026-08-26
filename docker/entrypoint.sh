#!/bin/sh
# Entrypoint: PATH для ffmpeg, bootstrap при отсутствии бинарников, затем CMD.
set -eu

FFMPEG_DIR="${FFMPEG_DIR:-/app/ffmpeg}"
export FFMPEG_DIR
export PATH="${FFMPEG_DIR}/bin:${PATH}"

# Автозагрузка BtbN FFmpeg, если бинарников ещё нет (пропуск при mount / SKIP)
python -c "from app.ffmpeg_bootstrap import ensure_ffmpeg; ensure_ffmpeg()"
# Чтобы run.py не вызывал ensure_ffmpeg повторно в том же контейнере
export FFMPEG_BOOTSTRAPPED=1

exec "$@"
