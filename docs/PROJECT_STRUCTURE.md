# Структура проекта al-video-kensetsu

Приложение принимает MP4 и MKV, создаёт задания кодирования и выдаёт готовые
MKV через веб-интерфейс и HTTP API. Backend построен на Flask и Flask-SocketIO,
интерфейс — на Jinja, Bootstrap и обычном JavaScript. FFmpeg кодирует потоки;
MKVToolNix обрабатывает контейнер и метаданные.

Для первого знакомства начните с `run.py`, `app/main.py`, `app/routes.py`
и `app/ffmpeg_worker.py`. Команды запуска и проверки находятся в
[DEVELOPMENT.md](DEVELOPMENT.md), внешний контракт — в [API.md](API.md).

## Каталоги исходного кода

```text
app/
├── __init__.py
├── main.py
├── routes.py
├── ffmpeg_worker.py
├── ffmpeg_bootstrap.py
├── mkvmerge_remux.py
├── audio_downmix.py
├── internal_source.py
├── output_export.py
├── file_utils.py
├── settings_store.py
├── job_store.py
├── logger.py
├── log_manager.py
├── models/
│   ├── __init__.py
│   ├── job.py
│   └── presets.py
├── assets/
│   └── empty.ass
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── settings.html
│   ├── logs.html
│   └── systeminfo.html
└── static/
    ├── js/
    │   ├── common.js
    │   ├── socket.js
    │   ├── upload.js
    │   ├── logs.js
    │   └── socket.io.min.js
    └── css/
        ├── main.css
        ├── theme.css
        └── components.css
```

`app/__init__.py` и `app/models/__init__.py` — пустые маркеры пакетов.

## Backend и обработка файлов

- [`run.py`](../run.py) — точка запуска сервера на `0.0.0.0:5000`.
  Проверяет FFmpeg перед импортом приложения; Docker Compose не запускает.
- [`app/main.py`](../app/main.py) — создаёт Flask, CORS и Socket.IO, регистрирует
  blueprint, создаёт воркер и связывает его callbacks с событиями для браузера.
  Восстанавливает историю из SQLite до приёма запросов. Здесь находятся `/health`
  и обработчики подключения Socket.IO; status callback сохраняет состояние задания.
- [`app/routes.py`](../app/routes.py) — страницы и HTTP API, приём файлов целиком
  и по частям, batch операции, внутренний источник, выбор параметров, настройки,
  логи и системная информация. `jobs_storage` — оперативный словарь заданий,
  восстановленный из SQLite при старте.
  `create_job_from_saved_file()` связывает исходник с заданием, вычисляет SHA256
  и ставит задание в очередь; `delete_job_by_id()` объединяет очистку при удалении.
- [`app/ffmpeg_worker.py`](../app/ffmpeg_worker.py) — FIFO очередь, диспетчер
  и отдельные потоки выполнения, лимит параллельности, построение команды FFmpeg,
  процессы, остановка, проверка хеша, прогресс и финализация результата.
  `_build_ffmpeg_command()` — основная точка изменения аргументов кодирования.
- [`app/models/job.py`](../app/models/job.py) — dataclass `Job`, статусы
  `planned`, `in_progress`, `done`, `error`, `cancelled`, timestamps в UTC,
  проверка хеша и сериализация для API. `get_output_filename()` оставляет на диске
  `{stem}_[HEVC]_{id[:8]}.mkv`; `get_download_filename()` возвращает готовое имя
  `{stem}_[HEVC].mkv` для API, скачивания и серверной копии.
  JSON включает точный внешний `input_path`,
  `request_id` и вычисляемую доступность финального файла. Рабочие пути
  `original_path`/`output_path` и SHA256 остаются только во внутренней записи.
- [`app/job_store.py`](../app/job_store.py) — SQLite заданий и квитанций
  internal encode: `jobs`, `encode_requests`, `request_jobs`. Атомарная резервация
  request_id до копирования, отпечаток переданного запроса, неизменяемый JSON
  ответа и исходный HTTP-код. Связи с job IDs сохраняются после удаления заданий.
  `recover()` при старте помечает незавершённые запросы `interrupted`, а задания
  `planned`/`in_progress` — `error`; повторного запуска нет.
- [`app/models/presets.py`](../app/models/presets.py) — модели четырёх слоёв,
  чтение YAML, defaults, объединение параметров и валидация downmix.
  Глобальный `preset_manager` создаётся при импорте. При отсутствующем или пустом
  слое создаёт встроенные пресеты. Другие заполненные слои не перезаписывает;
  при восстановлении пустого слоя может заменить одноимённые пустые YAML.
- [`app/internal_source.py`](../app/internal_source.py) — проверка разрешённых
  путей через `realpath`, снятие внешних кавычек, копирование из хранилища
  в uploads и выделение собственного исходника каждому заданию. Защищает
  библиотечные исходники и файлы, используемые другими заданиями, при очистке.
- [`app/output_export.py`](../app/output_export.py) — копирование готового результата
  в существующую папку из `INTERNAL_OUTPUT_ROOTS`, проверка realpath и конфликтов
  целевого имени. Без явного `overwrite: true` существующий файл не заменяется;
  внутренний результат и запись задания не меняются.
- [`app/file_utils.py`](../app/file_utils.py) — допустимые расширения `.mp4` /
  `.mkv`, безопасные имена, `get_outputs_dir()` (`OUTPUTS_DIR`), размер, SHA256 и удаление файлов. `get_video_info()`
  проверяет наличие и размер файла; сведения о потоках получают другие модули.
  SHA256 через `hashlib.file_digest` используется также в `Job.calculate_hash()`.
- [`app/audio_downmix.py`](../app/audio_downmix.py) — ffprobe аудиопотоков,
  выбор дорожек и построение `filter_complex` / `-map` для stereo downmix.
  ATSC LoRo без LFE применяется только к дорожкам с количеством каналов больше двух.
- [`app/mkvmerge_remux.py`](../app/mkvmerge_remux.py) — поиск mkvmerge и
  mkvpropedit, анализ MKV, имена/языки/флаги дорожек, ник и заголовок видео,
  исправление MIME вложенных шрифтов, JSON опций и пути временных файлов.
  [`app/assets/empty.ass`](../app/assets/empty.ass) добавляется как пустая дорожка
  «Надписи», когда этого требуют правила субтитров.
- [`app/ffmpeg_bootstrap.py`](../app/ffmpeg_bootstrap.py) — пути FFmpeg/ffprobe,
  runtime-загрузка Linux-сборки BtbN n9.0 GPL для amd64/arm64,
  проверка версии и доступности FDK. На других ОС использует готовые бинарники.
- [`app/settings_store.py`](../app/settings_store.py) — чтение и атомарная запись
  JSON под lock: `metadata_nickname` и `max_concurrent_encodes`. Сохранённые
  значения имеют приоритет над env, в том числе сохранённый пустой ник.
- [`app/logger.py`](../app/logger.py) — файловые `main.log`, `upload.log`,
  `ffmpeg.log`, консоль и передача записей в память. При недоступности каталога
  логов использует `logs/` относительно текущего рабочего каталога.
- [`app/log_manager.py`](../app/log_manager.py) — логи в RAM: системный поток
  до 5000 записей и до 10000 записей каждого задания; выдача для UI и callback
  рассылки. Очистка через HTTP API очищает память, а не файлы `.log`.

## Веб-интерфейс

[`app/templates/base.html`](../app/templates/base.html) задаёт навигацию,
общий layout, уведомления и модалку подтверждения. Порядок CSS:
`main.css → theme.css → components.css`. После Bootstrap и Socket.IO загружаются
`common.js → socket.js → скрипты страницы`.

- [`index.html`](../app/templates/index.html) — `/`: выбор источника,
  пресеты и параметры FFmpeg, метаданные, список заданий и batch действия.
  Значительная часть логики пресетов и сохранения ника находится в inline JS
  этого шаблона. Подключает `upload.js` и `logs.js`.
- [`settings.html`](../app/templates/settings.html) — `/settings`: лимит
  параллельности, inline JS для `GET` / `PUT /api/settings`.
- [`logs.html`](../app/templates/logs.html) — `/logs`: системный поток,
  выбор задания, пауза, обновление, очистка и таблица заданий; подключает `logs.js`.
- [`systeminfo.html`](../app/templates/systeminfo.html) — `/systeminfo`:
  inline JS опрашивает `/api/system-info` сразу и каждые пять секунд.
- [`common.js`](../app/static/js/common.js) — общие глобальные функции:
  даты в часовом поясе браузера, HTML escaping, уведомления, подтверждения,
  tooltips, карточки и строки заданий, выбор и одиночные/batch действия.
  Форма серверного сохранения и явное подтверждение перезаписи используют
  общую модалку из `base.html`.
- [`socket.js`](../app/static/js/socket.js) — одно подключение `window.socket`,
  reconnect, немедленный начальный `GET /api/jobs`, общий `jobsCache`, обработка
  `job_update` / `progress_update`. Объединяет REST и события, предотвращая
  откат статуса/прогресса устаревшим ответом. При недоступном Socket.IO опрашивает
  REST каждые 5 секунд без перекрытия запросов; скрытая вкладка не опрашивается.
  Показывает статус соединения в навигации; после connect прекращает polling.
- [`upload.js`](../app/static/js/upload.js) — отправка файлов и пути хранилища,
  прогресс загрузки. Небольшие файлы отправляет через XHR; большие — частями
  по 4 MiB через fetch с повторами. `addFormDataToFormData()` добавляет параметры,
  в том числе поля, расположенные вне `ffmpegForm`. Параметры фиксируются на всю
  отправку; частичный batch оставляет для повтора только непринятые файлы.
- [`logs.js`](../app/static/js/logs.js) — загрузка `/api/logs` и `/api/logs/jobs`,
  фильтрация `log_update` по потоку/заданию, пауза и очистка. На главной странице
  инициализация прекращается, поскольку DOM страницы логов отсутствует.
  `refreshFallbackLogs` обновляет логи через REST при потере Socket.IO с учётом паузы.
- [`socket.io.min.js`](../app/static/js/socket.io.min.js) — официальный локальный
  клиент Socket.IO 4.7.4; подключается в base.html. MIT license —
  [`socket.io.LICENSE`](../app/static/js/socket.io.LICENSE).
- [`main.css`](../app/static/css/main.css) — базовый layout и адаптивность;
  [`theme.css`](../app/static/css/theme.css) — тёмная тема;
  [`components.css`](../app/static/css/components.css) — задания, логи, toolbar,
  прогресс, кнопки и уведомления.

Сборщика frontend и npm-зависимостей нет. `base.html` подключает с CDN
Bootstrap CSS 5.3.2, Bootstrap JS 5.3.0 и Bootstrap Icons 1.10.0.
JS зависит от глобальных функций и идентификаторов элементов в шаблонах.

## Пресеты

[`presets/`](../presets) содержит четыре независимых слоя. В каждом нужен
ровно один `default: true`; некорректные defaults препятствуют запуску.

- `video/`: `hevc_auto_level_rip.yaml` — default, без заданного x265 level/ref;
  `hevc_rip.yaml` — с фиксированными level/ref;
  `hevc_remux.yaml` — libx265 с другими параметрами качества.
  Все три кодируют видео; имя `hevc_remux` не означает копирование видеопотока.
- `audio/`: `copy.yaml` — default; `opus.yaml`, `aac.yaml`, `aac_fdk.yaml`,
  `none.yaml`. Доступность FDK зависит от сборки FFmpeg.
- `subtitles/`: `copy.yaml` — default; `none.yaml`. Отдельного выбора в UI нет.
- `data/`: `copy.yaml` — default; `none.yaml`. В текущем mapping это вложения
  (`0:t?`, `-c:t`), например шрифты. Отдельного выбора в UI нет.

`audio_id=none` отключает также субтитры и вложения. `audio_downmix=stereo`
требует перекодирования аудио, несовместим с `copy` / `none`.
Изменения YAML подхватываются после перезапуска; HTTP endpoint перезагрузки нет.
При отсутствующем каталоге video генератор в `models/presets.py` создаёт все
три стандартных пресета с тем же default `hevc_auto_level_rip`.

## Запуск и инфраструктура

- [`docker/Dockerfile`](../docker/Dockerfile) — Python 3.15.0, runtime зависимости,
  MKVToolNix CLI и каталоги данных; копирует приложение и пресеты. Тесты и pytest
  в образ не включаются. FFmpeg скачивается после сборки, при запуске.
  `LANG` и `LC_ALL` явно заданы `C.UTF-8`: это необходимо MKVToolNix для
  корректной обработки кириллических путей.
  Wheels psutil/PyYAML собираются отдельным stage с компилятором и libyaml-dev;
  runtime получает готовые расширения и libyaml, без компилятора. Bytecode `app/`
  компилируется при сборке, запись новых pyc в runtime отключена.
- [`docker/entrypoint.sh`](../docker/entrypoint.sh) — PATH, bootstrap FFmpeg,
  `FFMPEG_BOOTSTRAPPED=1` и выполнение `CMD` (`python run.py`).
- [`docker-compose.yml`](../docker-compose.yml) — основной запуск с локальной
  сборкой, порт `3000:5000`, named volumes и bind-mount пресетов.
- [`docker-compose.dev.yml`](../docker-compose.dev.yml) — дополнительно
  монтирует `app/`; `run.py` задаёт `debug=False`, поэтому автоматического
  Python reload этот вариант не включает.
- [`docker-compose.prod.yml`](../docker-compose.prod.yml) — локальная сборка,
  production env, уровни логов и ограничения памяти.
- [`docker-compose.windows.yml`](../docker-compose.windows.yml) — основной
  вариант без монтирования Linux `/etc/localtime` и `/etc/timezone`.
- [`docker-compose.test.yml`](../docker-compose.test.yml) — отдельный сервер
  на порту 5001 с `test_*` томами. Команды pytest в нём нет.
- [`docker-compose.multiarch.yml`](../docker-compose.multiarch.yml) — сборка
  для `linux/amd64` и `linux/arm64`.
- [`docker-compose.registry.yml`](../docker-compose.registry.yml) и
  [`docker-compose.registry.windows.yml`](../docker-compose.registry.windows.yml)
  — готовый образ из registry, `pull_policy: always`; пресеты берутся из образа,
  если не включён дополнительный bind-mount.
- [`docker-compose.unraid.yml`](../docker-compose.unraid.yml) — фрагмент
  сервиса с путями `/mnt/user/appdata/...`; его нужно включить в `services:`
  полноценного Compose файла.
- [`start-windows.bat`](../start-windows.bat) — меню запуска, остановки,
  пересборки, логов и очистки Windows Compose.
- [`Makefile`](../Makefile) и [`Makefile.windows`](../Makefile.windows) —
  вспомогательные Docker команды. Особенности целей `test` — в [DEVELOPMENT.md](DEVELOPMENT.md).
- [`scripts/build-and-push.sh`](../scripts/build-and-push.sh) и
  [`scripts/build-and-push.ps1`](../scripts/build-and-push.ps1) — сборка/push,
  registry env, build metadata, увеличение [`build_number.txt`](../build_number.txt).
  [`scripts/publish.ps1`](../scripts/publish.ps1) — обёртка для multiarch push.
- [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml)
  — multiarch публикация по тегу `v*` или вручную; зависит от успешного Tests
  workflow и не увеличивает `build_number.txt`.
- [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) — Python
  3.15/3.15t, проверка GIL после native imports, pip check и JavaScript тесты.
- [`scripts/benchmark_runtime.py`](../scripts/benchmark_runtime.py) — изолированные
  замеры API с 1/4 потоками, памяти, режима GIL/JIT и SHA256; пишет JSON результатов.
- [`.python-version`](../.python-version) — версия локального Python 3.15.0.
- [`docs/benchmarks/`](benchmarks) — исходные JSON замеров Python 3.15/3.15t
  и сводка; методика и выбор runtime описаны в [DEVELOPMENT.md](DEVELOPMENT.md).
- [`requirements.txt`](../requirements.txt) — фиксированные runtime зависимости;
  [`requirements-dev.txt`](../requirements-dev.txt) — pytest.
- [`config.example.env`](../config.example.env) — пример env приложения,
  содержащий также исторические ключи; реально используемые — в [DEVELOPMENT.md](DEVELOPMENT.md).
  [`registry.example.env`](../registry.example.env) — пример параметров публикации.
- [`.gitignore`](../.gitignore) — исключения для runtime данных, env и кэшей;
  [`.dockerignore`](../.dockerignore) — исключения build context;
  [`.gitattributes`](../.gitattributes) — правила окончаний строк.

## Поток выполнения задания

1. Браузер или клиент API передаёт файл/файлы в `/api/upload` либо путь/пути
   в `/api/internal/encode`. ZIP на входе не поддерживается.
2. `routes.py` валидирует параметры. `internal_source.py` проверяет путь
   хранилища и копирует файл в uploads; библиотечный оригинал остаётся на месте.
   Чанки загруженного файла собираются в uploads перед созданием задания.
   Если передан `request_id`, SQLite резервирует его до проверки параметров
   и копирования. Повтор возвращает сохранённую квитанцию либо 202;
   несовпадающий отпечаток даёт 409.
3. `create_job_from_saved_file()` выделяет собственный исходник, вычисляет SHA256,
   создаёт `Job`, сохраняет в SQLite, добавляет его в `jobs_storage` и очередь.
   Внешний `input_path` не заменяется временной копией. Копирование и хеширование
   происходят синхронно в HTTP запросе.
4. Воркер занимает свободный слот, выставляет `in_progress`, повторно проверяет
   SHA256 и запускает FFmpeg. Строки вывода и прогресс поступают в callbacks.
5. При `update_metadata=true` (default) FFmpeg пишет `*.partial.mkv`, затем
   mkvmerge собирает результат через JSON опций и `*.remux.tmp`.
   Финальный MKV заменяется атомарно. При `update_metadata=false` FFmpeg
   пишет сразу в итоговый файл, remux пропускается.
6. После финализации статус становится `done`; временный исходник удаляется.
   Status callback сохраняет Job в SQLite; прогресс между сменами статуса живёт
   в RAM. HTTP / Socket.IO вычисляют доступность файла без чтения видео.
   Ошибка или остановка активного задания очищает незавершённые файлы.
   Остановленный до запуска исходник остаётся до удаления задания.
7. Готовый результат скачивается отдельно или пакетным ZIP без сжатия, с именем
   без суффикса задания. В ZIP совпадающие имена получают ` (2)`, ` (3)`.
   Через `POST /api/jobs/<id>/save` готовый файл можно скопировать в разрешённую
   папку сервера; существующий файл требует подтверждения перезаписи.
   Удаление задания очищает его данные и запись в памяти/SQLite; квитанция остаётся.

События Socket.IO: `job_update` передаёт сериализованный Job,
`progress_update` — `job_id` и `progress`, `log_update` — поток, job ID,
сообщение, timestamp и уровень. Готовность результата определяет `status=done`:
100% прогресса FFmpeg может предшествовать завершению remux.
Процент приблизительный: при отсутствии `NUMBER_OF_FRAMES` используется
оценка длительности 7200 секунд, а при `time=N/A` — также 30 fps.

## Данные во время работы

- `/app/uploads` — загрузки, каталоги чанков `temp_{file_id}` и копии исходников
  для заданий. Может быть изменён через `UPLOADS_DIR`.
- `/app/outputs` — готовые MKV и временные файлы обработки. Путь задаётся через
  `OUTPUTS_DIR`; исторический env `OUTPUT_FOLDER` не читается.
- `/app/logs` — файловые логи; путь может быть изменён через `LOG_DIR`.
- `/app/settings/app_settings.json` — сохранённый ник и параллельность;
  путь может быть изменён через `APP_SETTINGS_PATH`.
- `/app/settings/jobs.sqlite3` — задания и квитанции; путь задаётся через
  `JOB_STORE_PATH` независимо от `APP_SETTINGS_PATH`. Том settings сохраняет
  идемпотентность после замены контейнера. Нужен доступ на запись к каталогу
  базы и служебным SQLite файлам.
- `/app/data` — дополнительный каталог, в том числе legacy источник настроек.
  `settings_store.py` также читает старые настройки из `/app/logs`.
- `/app/ffmpeg/bin` — FFmpeg и ffprobe в томе `ffmpeg_bin`;
  каталог задаётся через `FFMPEG_DIR`.
- `/anilibria` — разрешённый по умолчанию корень внешнего хранилища;
  сам mount надо подключить в Compose. Корни задаются через `INTERNAL_SOURCE_ROOTS`.
- `/exports` — пример отдельного bind-mount для готовых копий с доступом на запись.
  Для него задайте `INTERNAL_OUTPUT_ROOTS=/exports`. Без override корень сохранения
  — `/anilibria`; явно пустое значение отключает серверное сохранение.

Точный набор named volumes зависит от Compose варианта. Это пути внутри
контейнера; одноимённые каталоги в checkout не появляются без bind-mount.
Локальный fallback логов и прочие runtime каталоги исключаются из Git.

Jobs и квитанции сохраняются в SQLite и восстанавливаются при старте;
незавершённые задания получают `error`, запросы — `interrupted`.
Очередь, учёт медиапроцессов и live логи остаются в RAM одного Python процесса.
FFmpeg и mkvmerge запускаются отдельными процессами ОС. Из файлов outputs задания
автоматически не восстанавливаются; существовавшая до внедрения SQLite история
из RAM не мигрирует. Поддерживается один WSGI worker: несколько серверных процессов
имеют раздельные очереди и небезопасно выполняют восстановление общей базы.

## Тесты и документы

Все тесты находятся в [`tests/`](../tests); назначение каждого файла и команды
приведены в [DEVELOPMENT.md](DEVELOPMENT.md). `tests/__init__.py` — маркер пакета.
[`tests/test_output_export.py`](../tests/test_output_export.py) проверяет серверные
копии, границы разрешённых каталогов и подтверждение перезаписи; имена скачивания
и коллизии ZIP покрыты `tests/test_concurrent_and_batch.py`.

- [`README.md`](../README.md) — возможности и основные способы запуска.
- [`QUICKSTART.md`](../QUICKSTART.md) — краткий запуск и использование.
- [`EXAMPLES.md`](../EXAMPLES.md) — пользовательские сценарии и параметры.
- [`docs/API.md`](API.md) — контракт HTTP API для интеграций.
- [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) — разработка, конфигурация и тесты.
- [`WINDOWS_SETUP.md`](../WINDOWS_SETUP.md) — запуск через Docker Desktop.
- [`MULTIARCH.md`](../MULTIARCH.md) — архитектуры и сборка образов.
- [`WEBSOCKET_TROUBLESHOOTING.md`](../WEBSOCKET_TROUBLESHOOTING.md) — диагностика Socket.IO.
- [`COMPOSE_UPDATES.md`](../COMPOSE_UPDATES.md) — сведения об изменениях Compose.
- [`CHANGELOG.md`](../CHANGELOG.md) — история поведения по версиям.
- [`AGENTS.md`](../AGENTS.md) — краткие заметки и правила дальнейшей работы;
  [`.cursor/rules/documentation.mdc`](../.cursor/rules/documentation.mdc)
  подключает правило поддержания документации в Cursor.

Изменения структуры, потоков и расположения данных обновляются в этом файле
вместе с соответствующими правками кода. Правила синхронизации документов
описаны в [DEVELOPMENT.md](DEVELOPMENT.md).
