#!/usr/bin/env python3
"""
Скрипт для запуска приложения кодирования видео
"""

import os
import sys

# Добавляем путь к приложению
sys.path.insert(0, '/app')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == '__main__':
    # Entrypoint уже вызвал ensure_ffmpeg (FFMPEG_BOOTSTRAPPED=1);
    # локальный запуск без entrypoint — проверяем здесь.
    if os.environ.get('FFMPEG_BOOTSTRAPPED', '').strip().lower() not in (
        '1', 'true', 'yes', 'on',
    ):
        from app.ffmpeg_bootstrap import ensure_ffmpeg
        ensure_ffmpeg()

    from app.main import app, socketio
    from app.main import ffmpeg_worker
    import atexit
    import signal

    def shutdown(signum, frame):
        ffmpeg_worker.stop_worker()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    atexit.register(ffmpeg_worker.stop_worker)

    print("🎬 Запуск системы кодирования видео")
    print("=" * 50)
    print("Приложение будет доступно по адресу: http://0.0.0.0:5000")
    print("WebSocket подключения разрешены со всех источников")
    print("-" * 50)

    # Запускаем приложение с Socket.IO
    socketio.run(app,
                host='0.0.0.0',
                port=5000,
                debug=False,
                allow_unsafe_werkzeug=True)
