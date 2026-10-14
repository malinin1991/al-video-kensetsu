# Заметки для работы над проектом

## Контекст и навигация

- Это Flask / Flask-SocketIO приложение для кодирования MP4/MKV через FFmpeg
  и последующей обработки MKV через MKVToolNix. Frontend — Jinja и глобальный JS без npm.
- Полная карта файлов: [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).
- Запуск, env, тесты и места изменений: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
- Внешний HTTP контракт: [docs/API.md](docs/API.md). Документацию вести на русском.

## Важные особенности

- `run.py` запускает Python сервер на 5000. Основной Compose публикует его на 3000.
  Импорт `app.main` создаёт лог-файлы и фоновый воркер; он имеет побочные эффекты.
- Jobs и квитанции internal encode сохраняются в SQLite (`app/job_store.py`,
  `JOB_STORE_PATH`, default `/app/settings/jobs.sqlite3`). При старте история
  восстанавливается; `planned`/`in_progress` становятся `error` без автозапуска.
  Очередь и live логи остаются в RAM одного процесса; outputs сами не создают Jobs.
- `input_path` — точная строка internal `path`/`paths`, отдельно от временного
  `original_path`. `request_id` резервируется до копирования; готовый ответ
  неизменяем. GET квитанции — `/api/internal/encode/requests/<request_id>`;
  `processing`/`interrupted` дают 202, конфликт параметров — 409.
- `presets/` содержит слои video/audio/subtitles/data; в каждом ровно один default.
  YAML загружаются при импорте; после изменений нужен restart.
- `hevc_remux` тоже перекодирует видео. Remux и правила метаданных реализованы
  отдельно в `app/mkvmerge_remux.py`; FFmpeg metadata flags не заменяют этот этап.
- `update_metadata=true` использует partial → remux tmp → атомарный итоговый MKV.
  Готовность определяется статусом `done`, а не достижением 100% прогресса FFmpeg.
- Каждому заданию нужен собственный временный исходник. При изменениях очистки
  сохранять библиотечные и используемые другими заданиями файлы.
- `UPLOADS_DIR`, `LOG_DIR`, `APP_SETTINGS_PATH` настраиваются; outputs жёстко
  `/app/outputs`. Сохранённые настройки приоритетнее env, включая пустой nickname.
- Пресеты и metadata UI частично реализованы inline в `index.html`;
  `settings.html` и `systeminfo.html` также содержат inline JS.
- Общие карточки и строки Job — `common.js`; обновления и кэш — `socket.js`.
  Поля вне `ffmpegForm` собираются явно в `upload.js`.
- ZIP поддерживается для batch download. Загрузка принимает только MP4/MKV.

## Проверка изменений

- Установить `requirements.txt` и `requirements-dev.txt`; команда тестов:
  `python -m pytest -q tests` или подходящие затронутые файлы.
- Тесты подменяют медиапроцессы; они не заменяют проверку реального кодирования.
- `docker-compose.test.yml` и `make test` поднимают сервер, а не запускают pytest.
- После документационных правок проверить относительные ссылки и `git diff --check`.

## Документация при каждой правке

- Обновлять затронутые документы вместе с изменениями кода и конфигурации.
- Файлы/ответственность/потоки — `docs/PROJECT_STRUCTURE.md`; API — `docs/API.md`;
  команды/env/тесты — `docs/DEVELOPMENT.md`; сценарии — README/QUICKSTART/EXAMPLES.
- Важные новые ограничения и точки входа добавлять сюда. Значимые изменения
  поведения отражать в CHANGELOG, не переписывая историю версий.
- Удалять устаревшие факты, избегать дублирования подробного контракта API.
  Для правки без изменения описанной структуры или поведения обновление docs не нужно.
