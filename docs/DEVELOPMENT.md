# Разработка al-video-kensetsu

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
Контейнер явно использует `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`: без UTF-8 locale
MKVToolNix может терять кириллическую часть имени файла и считать входом каталог.

`docker-compose.test.yml` / `make test-server` поднимают отдельный сервер на
`http://localhost:5001` с тестовыми томами. Они не запускают автоматические тесты.

## Локальный Python запуск

Dockerfile использует Python 3.15.0. Для локальной разработки создайте окружение
и установите runtime зависимости вместе с pytest:

```bash
python3.15 -m venv .venv
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

Пути загрузок, результатов, логов и настроек можно вынести в доступные локальные
каталоги через `UPLOADS_DIR`, `OUTPUTS_DIR`, `LOG_DIR`, `APP_SETTINGS_PATH`,
`JOB_STORE_PATH`. Например, после подготовки бинарников:

```bash
UPLOADS_DIR="$PWD/uploads" OUTPUTS_DIR="$PWD/outputs" LOG_DIR="$PWD/logs" \
APP_SETTINGS_PATH="$PWD/settings/app_settings.json" \
JOB_STORE_PATH="$PWD/settings/jobs.sqlite3" python run.py
```

Каждый модуль создаёт используемый каталог по фактическому пути. Воркер создаёт
outputs перед кодированием; `main.py` не пытается создавать фиксированные `/app/*`.

## Выбор Python 3.15 и оптимизации

Основной runtime — **стандартный CPython 3.15.0** из
`python:3.15.0-slim-trixie`; `.python-version` закрепляет ту же версию.
3.15t проверен с действительно выключенным GIL после импорта Flask, Socket.IO,
psutil и C-расширения PyYAML. Он остаётся в CI как проверяемый альтернативный runtime.

Обновлены Flask-SocketIO **5.7.0**, python-socketio **5.17.0**, python-engineio
**4.14.0**, Werkzeug **3.1.9**, pytest **9.1.1**. Flask 3.1.3, Flask-CORS 6.0.5,
psutil 7.2.2 и PyYAML 6.0.3 уже соответствовали актуальным версиям PyPI на 17.10.2026.

Применены следующие оптимизации для выбранного runtime:

- Официальный CPython собран с PGO/LTO; собственная пересборка интерпретатора не нужна.
- Приём файла и `Job.verify_hash()` используют общую SHA256 реализацию через
  `hashlib.file_digest`, без Python-цикла с блоками по 4 KiB. Хеш и HTTP контракт
  не изменены. На прогретом файле 64 MiB замер показал примерно **11% больше MiB/s**.
- Пресеты читаются через безопасный **CSafeLoader**, с fallback на SafeLoader.
  Python tags по-прежнему запрещены. На 500 разборах одного стандартного пресета
  C-loader оказался примерно **10.7 раза быстрее**; это замер парсинга, а не ускорение
  всего старта или кодирования.
- Docker собирает native wheels отдельным stage с gcc/libyaml-dev и проверяет
  наличие C-ускорителя. В runtime остаётся libyaml, компилятор не устанавливается.
- Bytecode `app/` создаётся при сборке с обычной семантикой Python, без `-O`/`-OO`;
  `PYTHONDONTWRITEBYTECODE=1` исключает запись кэшей во время работы.

JIT в проверенном официальном Linux arm64 образе недоступен
(`sys._jit.is_available() == False`), поэтому флаг `PYTHON_JIT` не задаётся.
Глобальный lazy imports также не включён: загрузка пресетов, восстановление SQLite
и создание воркера должны оставаться явными шагами старта.

### Сравнение 3.15 / 3.15t

Оба интерпретатора сравнивались последовательно в одном Linux arm64 окружении
с одинаковыми версиями библиотек, PGO/LTO, native PyYAML, лимитами **4 vCPU / 1 GiB**.
Порядок блоков: regular → t → t → regular; по пять раундов в блоке, итого десять
на API-сценарий. История — 200 заданий, 200 запросов на раунд, Flask test client
без сети; это измерение обработки API, а не пропускной способности HTTP сервера.

- `GET /api/jobs`, один клиент: **618 req/s** у 3.15 против **532 req/s** у 3.15t;
  стандартный runtime быстрее примерно на **16%**.
- Тот же endpoint, четыре потока: **636 / 980 req/s**; 3.15t быстрее примерно на **54%**.
- GET готовой квитанции: один клиент **4811 / 4383 req/s**, четыре потока
  **3286 / 9240 req/s**. Параллельный сценарий выигрывает от free-threading.
- Пиковый RSS Python-процесса: **57.6 MiB** у 3.15 и **120.7–127.0 MiB** у 3.15t.
  Память процессов FFmpeg в этот замер не входит.

Для локального приложения выбран обычный **3.15**: один пользователь не создаёт
сотни параллельных API-запросов, а кодирование выполняют отдельные FFmpeg/MKVToolNix.
Экономия памяти и скорость одного клиента полезнее дополнительной пропускной
способности API. Это решение для текущего сценария, не общий вывод о 3.15t.
Скорость самого кодека этими API-замерами не измерялась.

Исходные замеры и полные раунды сохранены в
[сводке](benchmarks/python315-summary.json),
[3.15: блок 1](benchmarks/python315-regular-1.json),
[3.15: блок 2](benchmarks/python315-regular-2.json),
[3.15t: блок 1](benchmarks/python315-freethreaded-1.json),
[3.15t: блок 2](benchmarks/python315-freethreaded-2.json).
Для повторения установите requirements в отдельные окружения обоих интерпретаторов:

```bash
python3.15 scripts/benchmark_runtime.py --output /tmp/encoder315.json
python3.15t scripts/benchmark_runtime.py --output /tmp/encoder315t.json
```

Скрипт использует временные данные/SQLite, не меняет рабочую историю и проверяет
GIL после native imports. SHA256 измеряется с прогретым файловым кэшем; YAML —
в памяти. При установке 3.15t без готовых wheels нужны инструменты сборки расширений
(на Debian: gcc, libc6-dev, libyaml-dev). В замерах `PYTHON_GIL` не задавался;
CI проверяет обычное поведение расширений после импорта.

Проверки миграции: **319 passed** на 3.15 и **319 passed** на 3.15t, включая два
настоящих медиатеста. Финальный Docker-образ дополнительно прошёл все **319**
проверок с обновлёнными runtime-библиотеками и MKVToolNix 102.0; FFmpeg 7.1.5
был предоставлен как внешний бинарник для теста. **14/14** JS проверок и
`pip check` обоих runtime успешны. Штатный entrypoint/run.py проверен в браузере:
страница диагностики показывает Python 3.15.0, Socket.IO подключён, ошибок клиента нет.
Без `RUN_REAL_MEDIA_TESTS=1` обычный pytest даёт **317 passed, 2 skipped**.

Официальные сведения:
[релиз 3.15.0](https://www.python.org/downloads/release/python-3150/),
[free-threading](https://docs.python.org/3.15/howto/free-threading-python.html),
[оптимизации 3.15](https://docs.python.org/3.15/whatsnew/3.15.html#optimizations),
[сборка Docker Python](https://github.com/docker-library/python/blob/master/3.15/slim-trixie/Dockerfile).

## Конфигурация приложения

Python не читает `.env` самостоятельно. При локальном запуске переменные нужно
передать процессу. Compose использует `.env` / `--env-file` для подстановки
в YAML; внутрь контейнера попадают переменные из `environment:` / `env_file:`,
а не все ключи файла автоматически.

Реально используемые настройки и места чтения:

- `SECRET_KEY`, `CORS_ORIGINS`, `SOCKETIO_ASYNC_MODE` — `app/main.py`.
  Socket.IO использует `threading` по умолчанию; `gevent` требует отдельной
  установки. `eventlet` не поддерживается.
  Default origins включают локальные 3000 и 5000; для другого порта укажите
  его origin в `CORS_ORIGINS`.
- `FLASK_ENV` — отображение окружения и подробность логов. Сам по себе не
  меняет `debug=False` в `run.py`.
- `UPLOADS_DIR` — `app/internal_source.py`; default `/app/uploads`.
- `OUTPUTS_DIR` — `app/file_utils.py`; default `/app/outputs`.
  Воркер создаёт каталог перед encode. Путь результата сохраняется в SQLite:
  смена env влияет на новые задания, а уже готовые продолжают использовать
  сохранённый путь. Существующие файлы автоматически не переносятся.
- `INTERNAL_SOURCE_ROOTS` — `app/internal_source.py`; список корней через запятую,
  default `/anilibria`. Явно пустая строка отключает внутренний источник.
  Каталог хранилища нужно отдельно смонтировать в контейнер, обычно read-only.
- `INTERNAL_OUTPUT_ROOTS` — `app/output_export.py`; разрешённые корни серверных
  копий через запятую, default `/anilibria`. Пустое значение отключает сохранение.
  Нужен существующий realpath-корень и доступ на запись. `create_directories: true`
  в `/save` создаёт недостающие подкаталоги внутри него; без флага API требует
  существующую папку. Настройка независима от `OUTPUTS_DIR`
  (внутренние результаты) и `INTERNAL_SOURCE_ROOTS` (чтение исходников).
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

### Папка для готовых копий в Docker

Основной Compose и оба registry-варианта передают
`INTERNAL_OUTPUT_ROOTS` через `environment`; можно задать значение в `.env`
или `registry.env`. Подстановка `${INTERNAL_OUTPUT_ROOTS-/anilibria}` сохраняет
явно пустое значение как отключение функции. Unraid настроен отдельно ниже.
Остальные Compose-варианты требуют
добавить переменную в `environment` вручную: один `.env` не передаёт её в контейнер.

В основном Compose и registry-вариантах для отдельного каталога раскомментируйте
пример writable bind-mount
`/mnt/user/anilibria-encoded:/exports` (на Windows — `./data/exports:/exports`)
и задайте `INTERNAL_OUTPUT_ROOTS=/exports`. Создайте на хосте корневой каталог
заранее, дайте процессу контейнера право записи и пересоздайте контейнер после
изменения конфигурации. В интерфейсе указывайте `/exports` или папку внутри него;
включённая по умолчанию опция «Создать недостающие папки» создаёт подкаталоги.
Mount исходников `/anilibria:ro` оставляйте только для чтения.
Без отдельного mount готовая копия остаётся внутри контейнера.

В `docker-compose.unraid.yml` mount `/mnt/user/anilibria:/anilibria:rw` уже
разрешает читать исходники и сохранять готовые копии в общей папке хоста.
`INTERNAL_SOURCE_ROOTS` и `INTERNAL_OUTPUT_ROOTS` явно равны `/anilibria`;
значение output roots из `.env` этот вариант не подставляет. В UI/API используйте
`/anilibria` и его подкаталоги, например `/anilibria/encoded/Show/season-01` для копий.
Подготовьте корневую папку и права записи для процесса контейнера, затем
пересоздайте контейнер после изменения mount или `environment`. Серверное
сохранение требует актуального образа с endpoint `POST /api/jobs/<id>/save`;
отправка коммита в `main` сама по себе образ не публикует.

API по умолчанию сохраняет только в существующую папку (`create_directories: false`);
в UI создание подкаталогов включено. `true` создаёт всё недостающее дерево внутри
уже существующего разрешённого корня, но не сам корень и не папки вне него.
Валидация флагов, готовности и наличия итогового файла выполняется до
создания каталогов. При ошибке копирования созданные пустые папки остаются.

`app/output_export.py` сначала пишет временную копию в целевой каталог и
публикует её целиком. Без перезаписи публикация использует hardlink в той же
файловой системе: если хранилище не поддерживает эту операцию, API возвращает
ошибку, сохраняя существующий файл. Копия получает rw-права рабочего результата
без execute/special bits. На платформах без `dir_fd` (в том числе Windows)
переименование целевой папки во время копирования может оставить в ней
`.kensetsu-export-*.tmp`; приложение отклонит сохранение и запишет предупреждение.
В Linux/Docker временный файл очищается через открытый дескриптор каталога.

`config.example.env` содержит исторические ключи, не читаемые текущим приложением:
`UPLOAD_FOLDER`, `OUTPUT_FOLDER`, `LOG_FOLDER`, `MAX_CONTENT_LENGTH`,
`MAX_FILE_SIZE`, `ALLOWED_EXTENSIONS`, `FFMPEG_THREADS`, `FFMPEG_PRESET`,
`FFMPEG_CRF`, `DOCKER_MEMORY_LIMIT`, `DOCKER_CPU_LIMIT`, `LOG_FORMAT`.
Максимальный размер HTTP запроса задан в `app/main.py` как 16 GiB;
тот же лимит ограничивает сумму загруженных частей одного файла. Превышение
возвращает JSON и HTTP 413. Допустимые расширения заданы в `app/file_utils.py`.
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

Frontend проверяется встроенным runner Node.js 22+, без npm и зависимостей:

```bash
node --test tests/js/*.test.cjs
make test
```

`make test` запускает оба набора; `make test-python` и `make test-js` — отдельно.
Переменные `PYTHON` / `NODE` позволяют выбрать интерпретаторы.

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
- `tests/test_output_export.py` — серверная копия, разрешённые корни,
  realpath/symlinks, создание дерева подкаталогов, конфликты и явная перезапись;
  сохранность внутреннего результата и целевого файла при отказе.
- `tests/test_audio_downmix.py` — аудиокарты, ffprobe, pan и команда FFmpeg.
- `tests/test_mkvmerge_remux.py` — названия, языки, флаги, ник, JSON опций,
  пустые субтитры, MIME шрифтов и поиск бинарников.
- `tests/test_ffmpeg_worker_metadata.py` — аргументы FFmpeg, порядок remux,
  отмена, ошибки, временные файлы и сохранность исходников.
- `tests/test_concurrent_and_batch.py` — параллельные потоки, изменение лимита,
  пакетный ZIP, готовые имена без суффикса задания, коллизии имён и удаление.
- `tests/test_settings_store.py` — атомарное сохранение, приоритет JSON над env,
  валидация и чтение legacy настроек.
- `tests/test_settings_api.py` — HTTP настройки, применение лимита и страница настроек.
- `tests/test_file_hash.py` — одинаковый SHA256 загрузки и Job, границы блоков,
  пустые/изменённые/отсутствующие исходники.
- `tests/test_upload_safety.py` — chunk пути/имена/номера/лимиты, сохранность частей
  при ошибке сборки, JSON настройки, параметры ресурсов, недоступный uptime,
  гонки очереди/удаления/завершения, остановка процессов при ошибке чтения вывода.
- `tests/test_job_store_invariants.py` — неизменяемость опубликованных квитанций,
  конкурентная публикация и запоздавшее сохранение удалённого задания.
- `tests/test_socketio_isolation.py` — отдельная начальная история новому клиенту,
  общие обновления статусов и прогресса.
- `tests/js/frontend.test.cjs` — HTML escaping, частичные batch ответы,
  фиксация параметров, прогресс/завершение и доступность скачивания.
- `tests/test_real_media.py` — opt-in настоящий HEVC encode, MKVToolNix remux,
  флаги и имя видеодорожки, Copy 5.1 / Opus stereo, скачивание и повтор квитанции.

Тесты используют Flask test client, временные файлы и mock/fake вместо
кодирования и remux. Проверки параллельности используют настоящие Python потоки.
Обычный набор не запускает браузер, реальные медиапроцессы, bootstrap загрузку,
Docker сборку или публикацию. Для настоящего smoke нужны рабочие FFmpeg,
ffprobe, mkvmerge и mkvpropedit:

```bash
RUN_REAL_MEDIA_TESTS=1 python -m pytest -q tests/test_real_media.py
```

Тест сам генерирует синтетическое видео длительностью одну секунду во временном
каталоге. Пути бинарников можно передать через соответствующие env.
Импорт `app.main` создаёт воркер и лог-файлы;
для изоляции используйте временные пути и подменяйте воркер в API тестах.

При недоступном `psutil.boot_time()` (например, запрет `sysctl` в macOS sandbox)
`/api/system-info` возвращает HTTP 200 с `uptime: "Недоступно"` и
`system_status: "warning"`, сохраняя остальные сведения.

`.github/workflows/tests.yml` проверяет backend на Python 3.15/3.15t и frontend
на Node.js 22 при push/PR. Docker Publish вызывает этот workflow и ждёт успешных
проверок до сборки/публикации. Opt-in медиатест в обычном CI пропускается.
Временные каталоги через `runner.temp` задаются в `env` шага backend-тестов:
на уровне `jobs.<job>.env` этот context недоступен. Workflow можно проверить
валидатором `actionlint .github/workflows/*.yml` до отправки изменений.
Windows `Makefile.windows` сохраняет отдельный старый сценарий; для pytest
используйте прямую команду выше.

Официальный браузерный Socket.IO 4.7.4 хранится локально в
`app/static/js/socket.io.min.js`, источник —
[cdn.socket.io](https://cdn.socket.io/4.7.4/socket.io.min.js), MIT license рядом
в `socket.io.LICENSE`. После обновления проверьте синтаксис (`node --check`)
и frontend тесты. Bootstrap/Icons по-прежнему подключаются с CDN.

## Результаты ревью 17 октября 2026

Ревью провели четыре специалиста; перечисленные замечания реализованы и закрыты
регрессионными проверками. Авторизация исключена по условиям локального использования.

- **UI/UX:** точный результат частичной пачки и повтор только непринятых файлов;
  фиксация параметров на всю отправку; блокировка повторной отправки и смены
  источника; финализация на 100% отдельно от готовности; недоступный файл без
  download; HTML escaping имён; активная навигация, подписи действий и мобильные
  карточки; явный статус соединения и HTTP fallback заданий/логов.
- **Разработчик:** P1 — выход за каталоги через chunk ID/filename, потеря частей
  при сборке, гонка статуса после enqueue, удаление до завершения preflight/remux,
  живые процессы после ошибки чтения stdout. P2 — валидация ресурсов до копирования,
  413 вместо 500, общий лимит чанков, JSON настройки и недоступный uptime.
- **Аналитик:** согласованы HTTP 400/409/413 и критерии безопасного повтора частей,
  приёмки частичной пачки и отсутствия изменения настроек при неверном вводе.
  Bool/дроби для числа параллельных заданий отклоняются и в UI, и на сервере.
- **Архитектор:** готовая/прерванная квитанция не перезаписывается, конкурентная
  публикация имеет одного победителя; начальная Socket.IO история отправляется
  только новому клиенту; `OUTPUTS_DIR` делает локальные пути настраиваемыми;
  UTF-8 locale сохраняет Unicode пути MKVToolNix; CI проверяет backend/frontend
  и блокирует публикацию при сбое тестов; `make test` действительно запускает тесты.

Проверка включает обычные Python/JS наборы, браузерные сценарии и отдельный
настоящий медиатест HEVC + Copy 5.1 / Opus stereo, метаданные, скачивание и
неизменяемый повтор квитанции. Модель одного серверного процесса сохраняется;
текущая очередь и live логи остаются в памяти, незавершённые задания после
перезапуска требуют явного повтора. Это действующие границы локальной системы.

Финальные результаты этого ревью: **308 passed, 2 skipped** на Python 3.13
локально и Python 3.14 в изолированном runtime-контейнере; **14/14** JS проверок.
Два пропущенных по умолчанию медиатеста запущены отдельно с реальными FFmpeg и
MKVToolNix — **2/2 passed**, включая отсутствие временных файлов после финализации.
В браузере проверены создание реального задания, готовая карточка и кнопка скачивания,
страницы настроек/логов/диагностики, ширины 395 и 1280 px. На узкой главной странице
ширина документа совпадает с viewport (395 px), горизонтального переполнения нет.
Относительные ссылки документации и `git diff --check` прошли проверку.

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
- Готовые имена: `models/job.py`; серверная копия — `output_export.py` и `/save`
  в `routes.py`; подтверждение перезаписи и действия карточек/строк — `common.js`.
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
