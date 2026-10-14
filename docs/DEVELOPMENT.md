# Разработка al-web-video-encoder

Расположение модулей и поток выполнения описаны в [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md).
HTTP контракт находится в [API.md](API.md). Backend запускается одним Python
процессом; frontend не требует npm или отдельной сборки.

## Запуск через Docker

Из корня репозитория:

```bash
docker compose up --build
```

Основной адрес — `http://localhost:3000`; сервер внутри контейнера слушает 5000.
Для разработки с монтированием исходного кода:

```bash
docker compose -f docker-compose.dev.yml up --build
```

В этом варианте `app/` и `presets/` берутся из checkout. `run.py` явно задаёт
`debug=False`: после изменения Python или YAML перезапустите процесс контейнера.
Пересборка нужна при изменении зависимостей, Dockerfile или файлов, копируемых
в образ без bind-mount.

```bash
docker compose -f docker-compose.dev.yml restart video-encoder
docker compose -f docker-compose.dev.yml logs -f video-encoder
```

На Windows используйте `docker-compose.windows.yml` или `start-windows.bat`;
подробности в [WINDOWS_SETUP.md](../WINDOWS_SETUP.md).

Entrypoint проверяет и при необходимости загружает FFmpeg в том `/app/ffmpeg`.
Свои FFmpeg и ffprobe задаются через mount или абсолютные пути бинарников.
MKVToolNix устанавливается в образ при сборке. `/health` проверяет живость
приложения; статус инструментов доступен через `/api/system-info`.

`docker-compose.test.yml` поднимает отдельный сервер на `http://localhost:5001`
с тестовыми томами. Он не запускает pytest.

## Локальный Python запуск

Dockerfile использует Python 3.14. Для локальной разработки создайте окружение
и установите runtime зависимости вместе с pytest:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

На Windows активация PowerShell: `.venv\Scripts\Activate.ps1`.

`python run.py` запускает сервер на порту 5000 и проверяет FFmpeg до импорта
приложения. `python -m app.main` запускает другой путь с `debug=True`, без
предварительного bootstrap. Для штатного запуска используйте `run.py`.

Для обработки файлов нужны исполняемые FFmpeg и ffprobe, а при включённом
обновлении метаданных — mkvmerge. mkvpropedit используется, если требуется
исправить MIME вложенных шрифтов. На macOS и Windows подготовьте бинарники
для своей ОС и задайте пути через env либо PATH. Автозагрузка BtbN предназначена
для Linux amd64/arm64.

Пути загрузок, логов и настроек можно вынести в доступные локальные каталоги
через `UPLOADS_DIR`, `LOG_DIR`, `APP_SETTINGS_PATH`, `JOB_STORE_PATH`. Результаты воркер пишет
в `/app/outputs`; env для изменения этого пути сейчас нет. Локальное кодирование
требует доступного каталога `/app/outputs`, поэтому основной Compose вариант
подготавливает пути данных автоматически.

## Конфигурация приложения

Python не читает `.env` самостоятельно. При локальном запуске переменные нужно
передать процессу. Compose использует `.env` / `--env-file` для подстановки
в YAML; внутрь контейнера попадают переменные из `environment:` / `env_file:`,
а не все ключи файла автоматически.

Реально используемые настройки и места чтения:

- `SECRET_KEY`, `CORS_ORIGINS`, `SOCKETIO_ASYNC_MODE` — `app/main.py`.
  Socket.IO использует `threading` по умолчанию; `gevent` требует отдельной
  установки. `eventlet` не поддерживается.
- `FLASK_ENV` — отображение окружения и подробность логов. Сам по себе не
  меняет `debug=False` в `run.py`.
- `UPLOADS_DIR` — `app/internal_source.py`; default `/app/uploads`.
- `INTERNAL_SOURCE_ROOTS` — там же; список корней через запятую,
  default `/anilibria`. Явно пустая строка отключает внутренний источник.
  Каталог хранилища нужно отдельно смонтировать в контейнер, обычно read-only.
- `APP_SETTINGS_PATH` — `app/settings_store.py`;
  default `/app/settings/app_settings.json`.
- `JOB_STORE_PATH` — `app/job_store.py`; default `/app/settings/jobs.sqlite3`.
  Задания и квитанции internal encode сохраняются в SQLite (stdlib, без новой
  зависимости). Для локального запуска задайте доступный путь, например
  `JOB_STORE_PATH="$PWD/settings/jobs.sqlite3" python run.py`.
  Изменение `APP_SETTINGS_PATH` не меняет путь SQLite. Сохраняйте базу и каталог
  на постоянном диске; текущие Compose варианты уже монтируют `/app/settings`.
  Импорт `app.main` восстанавливает историю, переводит незавершённые задания
  в `error`, квитанции — в `interrupted`; обработка автоматически не повторяется.
  Запускайте один серверный процесс. База должна быть доступна до старта сервера.
- `MAX_CONCURRENT_ENCODES` — fallback лимита до сохранения в JSON,
  default 1, диапазон 1–16. Изменение через `/settings` или API применяется
  к следующим слотам без прерывания текущих заданий.
- `METADATA_NICKNAME` — fallback ника до сохранения в JSON.
  `metadata_video_title` передаётся для текущей пачки и в settings не записывается.
- `FFMPEG_DIR`, `FFMPEG_BIN`, `FFPROBE_BIN`, `FFMPEG_DOWNLOAD_URL`,
  `FFMPEG_SKIP_DOWNLOAD` — `app/ffmpeg_bootstrap.py`: каталог, пути бинарников,
  архив для bootstrap и запрет загрузки. `FFMPEG_BOOTSTRAPPED` — служебный
  marker entrypoint, предотвращающий повторный bootstrap в `run.py`.
- `MKVMERGE_BIN`, `MKVPROPEDIT_BIN` — `app/mkvmerge_remux.py`;
  при отсутствии override инструменты ищутся в PATH.
- `LOG_DIR`, `LOG_LEVEL`, `CONSOLE_LOG_LEVEL`, `MEMORY_LOG_LEVEL` —
  `app/logger.py`: каталог и уровни файловых, консольных и UI логов.
- `TZ` — окружение контейнера для локального времени файловых логов.
  Job timestamps хранятся в UTC; UI показывает время браузера.

`config.example.env` содержит исторические ключи, не читаемые текущим приложением:
`UPLOAD_FOLDER`, `OUTPUT_FOLDER`, `LOG_FOLDER`, `MAX_CONTENT_LENGTH`,
`MAX_FILE_SIZE`, `ALLOWED_EXTENSIONS`, `FFMPEG_THREADS`, `FFMPEG_PRESET`,
`FFMPEG_CRF`, `DOCKER_MEMORY_LIMIT`, `DOCKER_CPU_LIMIT`, `LOG_FORMAT`.
Максимальный размер HTTP запроса задан в `app/main.py` как 16 GiB;
допустимые расширения заданы в `app/file_utils.py`.
`FFMPEG_THREAD_QUEUE_SIZE` из Compose также не читается кодом: аргумент
`-thread_queue_size` берётся из поля `thread_queue_size` параметров задания.

Для сборки и публикации используются отдельные ключи `REGISTRY`, `IMAGE_NAME`,
`IMAGE_TAG`, учётные данные registry, `BUILD_TIME` и `GIT_SHA`.
См. [registry.example.env](../registry.example.env), [README.md](../README.md)
и [MULTIARCH.md](../MULTIARCH.md). Скрипты `build-and-push` увеличивают
`build_number.txt`, в том числе при сборке без push.

## Тесты

Из корня, после установки обеих групп зависимостей:

```bash
python -m pytest -q tests
```

Для конкретной области можно передать один файл, например:

```bash
python -m pytest -q tests/test_presets_resolve.py
```

- `tests/test_presets_resolve.py` — YAML слои, defaults, merge, optional поля,
  fallback пресеты и совместимость downmix.
- `tests/test_batch_presets_api.py` — API пресетов, доступность FDK,
  валидация, batch upload/internal, частичный успех и поля system-info.
- `tests/test_internal_encode.py` — пути, кавычки, разрешённые корни, symlinks,
  отдельные копии исходников, параметры и очистка.
- `tests/test_encoder_receipts.py` — точный input_path в single/batch,
  доступность/HEAD, request_id, конфликты, частичный 207, конкуренция, удаление,
  прерывание и повтор после восстановления в новом Python-процессе.
  `tests/conftest.py` изолирует SQLite при импорте приложения и в каждом тесте.
- `tests/test_audio_downmix.py` — аудиокарты, ffprobe, pan и команда FFmpeg.
- `tests/test_mkvmerge_remux.py` — названия, языки, флаги, ник, JSON опций,
  пустые субтитры, MIME шрифтов и поиск бинарников.
- `tests/test_ffmpeg_worker_metadata.py` — аргументы FFmpeg, порядок remux,
  отмена, ошибки, временные файлы и сохранность исходников.
- `tests/test_concurrent_and_batch.py` — параллельные потоки, изменение лимита,
  пакетный ZIP и удаление.
- `tests/test_settings_store.py` — атомарное сохранение, приоритет JSON над env,
  валидация и чтение legacy настроек.
- `tests/test_settings_api.py` — HTTP настройки, применение лимита и страница настроек.

Тесты используют Flask test client, временные файлы и mock/fake вместо
кодирования и remux. Проверки параллельности используют настоящие Python потоки.
Набор не проверяет браузер, реальные медиапроцессы, bootstrap загрузку,
Docker сборку или публикацию. Импорт `app.main` создаёт воркер и лог-файлы;
для изоляции используйте временные пути и подменяйте воркер в API тестах.

В ограниченном macOS sandbox тест `test_system_info_ffmpeg_fields` может
получить HTTP 500: `psutil.boot_time()` требует доступа к `sysctl`, который
может быть запрещён окружением. Этот системный вызов тест не подменяет.

`make test` запускает Compose сервер; Windows цель `test` пытается вызвать
pytest в runtime образе, где нет tests и pytest. Для текущего набора используйте
прямую команду выше. CI публикации Docker образа этот набор не запускает.

## Где менять поведение

- Новый HTTP endpoint: `app/routes.py`, контракт `docs/API.md` и API тесты.
  Новый Socket.IO event: callbacks в `app/main.py` и клиент `socket.js`.
- Новый параметр FFmpeg: модель/merge в `app/models/presets.py`, чтение формы
  в `app/routes.py`, команда в `app/ffmpeg_worker.py`, поля и inline применение
  пресетов в `index.html`, сбор FormData в `upload.js`.
- Новый пресет: YAML соответствующего слоя; при новом поле также модель/merge.
  Сохраните единственный default в каждом слое.
- Метаданные и дорожки: `app/mkvmerge_remux.py`; порядок процессов и финализация
  — `app/ffmpeg_worker.py`. Stereo downmix — `app/audio_downmix.py`.
- Очередь, параллельность, отмена: `app/ffmpeg_worker.py`; настройки лимита
  — `settings_store.py`, API и `settings.html`.
- Пути и исходники: `internal_source.py`; результаты и временные remux файлы
  — worker/remux; удаление задания — `routes.py`.
- Данные Job и timestamps: `models/job.py`, карточки и строки в `common.js`,
  объединение событий и REST в `socket.js`.
- Сохранение заданий / идемпотентность internal encode: `job_store.py`,
  резервирование request_id и GET квитанции в `routes.py`, восстановление
  и сохранение смен статуса в `main.py`. Не подменяйте input_path рабочим original_path.
- Загрузка: `index.html`, `upload.js`, upload handlers в `routes.py`.
  Поле вне `ffmpegForm` нужно явно добавить в `addFormDataToFormData()`.
- Логи: `logger.py`, `log_manager.py`, callback в `main.py`, `logs.html` и `logs.js`.
- Общий UI: `base.html` и CSS; карточки и таблица заданий создаются отдельно
  в `common.js`, поэтому изменения отображения нужно проверять в обоих местах.

## Поддержание документации

Документы обновляются в той же правке, что код или конфигурация:

1. Добавление, перенос или удаление файла меняет `PROJECT_STRUCTURE.md`.
2. Изменение API, входных полей, событий, статусов и кодов ответа меняет `API.md`;
   события и поток обработки также отражаются в карте проекта.
3. Изменение команд, зависимостей, env, путей, томов или тестов меняет этот файл
   и соответствующую инструкцию запуска.
4. Изменение пользовательского сценария или состава пресетов меняет README,
   QUICKSTART и EXAMPLES в затронутых местах. Значимое поведение отмечается в CHANGELOG.
5. Изменение важных ограничений или точек входа обновляет `AGENTS.md`.

Используйте существующие документы по назначению, сохраняйте русский язык
и относительные ссылки внутри репозитория. Сверяйте имена, defaults и команды
с кодом; удаляйте устаревшие сведения. Проверяйте ссылки и `git diff --check`.
Мелкая внутренняя правка без изменения описанного поведения не требует
формального обновления всех документов.
