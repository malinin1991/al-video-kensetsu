# Заметки для работы над проектом

## Контекст и навигация

- Проект — `al-video-kensetsu`, репозиторий:
  [malinin1991/al-video-kensetsu](https://github.com/malinin1991/al-video-kensetsu).
- Это Flask / Flask-SocketIO приложение для кодирования MP4/MKV через FFmpeg
  и последующей обработки MKV через MKVToolNix. Frontend — Jinja и глобальный JS без npm.
- Полная карта файлов: [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).
- Запуск, env, тесты и места изменений: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
- Внешний HTTP контракт: [docs/API.md](docs/API.md). Документацию вести на русском.

## Важные особенности

- Runtime — стандартный CPython 3.15.0 (PGO/LTO), `.python-version` и Docker
  синхронизированы. CI проверяет также 3.15t и отсутствие скрытого включения GIL
  после native imports. Сравнение и границы замеров — `docs/DEVELOPMENT.md`.
- SHA256 загрузки и проверки Job используют один `hashlib.file_digest`.
  YAML читается через CSafeLoader при наличии C-ускорителя, иначе SafeLoader;
  небезопасные Python tags не разрешены. Docker собирает native wheels отдельным stage.
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
  Default video — `hevc_auto_level_rip` без фиксированных x265 level/ref.
  YAML загружаются при импорте; после изменений нужен restart.
- `hevc_remux` тоже перекодирует видео. Remux и правила метаданных реализованы
  отдельно в `app/mkvmerge_remux.py`; FFmpeg metadata flags не заменяют этот этап.
- `update_metadata=true` использует partial → remux tmp → атомарный итоговый MKV.
  Готовность определяется статусом `done`, а не достижением 100% прогресса FFmpeg.
- Каждому заданию нужен собственный временный исходник. При изменениях очистки
  сохранять библиотечные и используемые другими заданиями файлы.
- `UPLOADS_DIR`, `OUTPUTS_DIR`, `LOG_DIR`, `APP_SETTINGS_PATH` настраиваются;
  default outputs — `/app/outputs`. Сохранённые настройки приоритетнее env, включая пустой nickname.
- Пресеты и metadata UI частично реализованы inline в `index.html`;
  `settings.html` и `systeminfo.html` также содержат inline JS.
- Общие карточки и строки Job — `common.js`; обновления и кэш — `socket.js`.
  Поля вне `ffmpegForm` собираются явно в `upload.js`.
- На диске результат имеет суффикс `id[:8]`; `get_download_filename()` и API
  `output_filename` возвращают готовое имя без него. ZIP при совпадениях добавляет
  ` (2)`, ` (3)` перед расширением без суффикса ID задания.
- Сохранение готовой копии на сервер: `POST /api/jobs/<id>/save`, разрешённые
  каталоги — `INTERNAL_OUTPUT_ROOTS` (default `/anilibria`, пусто — выключено).
  Корень/mount должен существовать; `create_directories: true` создаёт недостающие
  подкаталоги внутри него (UI включает по умолчанию, API — нет). Пустые созданные
  папки при ошибке копирования остаются. Перезапись только после явного подтверждения
  с `overwrite: true`. Оригинальный output не переносится.
- ZIP поддерживается для batch download. Загрузка принимает только MP4/MKV.
- Chunk upload проверяет ID/номера/имя и общий размер файла до сборки;
  не удалять принятые части при ошибке. Удаление Job ждёт завершения его потока
  и медиапроцесса; при таймауте возвращает 409 без очистки файлов.

## Проверка изменений

- Установить `requirements.txt` и `requirements-dev.txt`; команда тестов:
  `python -m pytest -q tests` или подходящие затронутые файлы.
- Тесты подменяют медиапроцессы; они не заменяют проверку реального кодирования.
- `make test` запускает pytest и `node --test tests/js/*.test.cjs` (Node.js 22+).
  `docker-compose.test.yml` / `make test-server` поднимают сервер.
- Настоящие медиапроцессы: `RUN_REAL_MEDIA_TESTS=1 python -m pytest -q tests/test_real_media.py`.
- После документационных правок проверить относительные ссылки и `git diff --check`.

## Документация при каждой правке

- Обновлять затронутые документы вместе с изменениями кода и конфигурации.
- Файлы/ответственность/потоки — `docs/PROJECT_STRUCTURE.md`; API — `docs/API.md`;
  команды/env/тесты — `docs/DEVELOPMENT.md`; сценарии — README/QUICKSTART/EXAMPLES.
- Важные новые ограничения и точки входа добавлять сюда. Значимые изменения
  поведения отражать в CHANGELOG, не переписывая историю версий.
- Удалять устаревшие факты, избегать дублирования подробного контракта API.
  Для правки без изменения описанной структуры или поведения обновление docs не нужно.
