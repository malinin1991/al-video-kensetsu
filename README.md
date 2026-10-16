# Система пакетного кодирования видео

Веб-система для пакетного кодирования видео с использованием Python 3.15, Flask, Bootstrap, Docker и FFmpeg.

## Возможности

- **Загрузка файлов**: Поддержка одиночных и нескольких видеофайлов (.mp4, .mkv)
- **Кодирование с хранилища**: Обработка файлов из разрешённых каталогов внутри контейнера
- **Система пресетов**: Готовые настройки кодирования для различных сценариев
- **Real-time обновления**: Прогресс кодирования и логи обновляются в реальном времени через WebSocket
- **Настраиваемые параметры**: Полная настройка параметров FFmpeg через веб-интерфейс
- **Расширенные настройки аудио**: Битрейт и частота дискретизации для AAC/Opus
- **Управление заданиями**: Просмотр, остановка, скачивание и удаление заданий
- **Автоматическая очистка**: Временные копии исходников удаляются после успешного кодирования; файлы хранилища сохраняются

## Технологии

- **Backend**: Python 3.15, Flask, Flask-SocketIO
- **Frontend**: Bootstrap 5, JavaScript, WebSocket
- **Кодирование**: FFmpeg с поддержкой H.265/HEVC
- **Конфигурация**: YAML пресеты для удобного управления настройками
- **Контейнеризация**: Docker, Docker Compose
- **Real-time**: WebSocket для live обновлений

## Структура проекта

Подробная карта файлов и поток обработки: **[docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)**.
Команды разработки, конфигурация и тесты: **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.
Краткие заметки и правила для агента: **[AGENTS.md](AGENTS.md)**.

```
al-web-video-encoder/
├── run.py                   # Запуск сервера Socket.IO на порту 5000
├── app/
│   ├── main.py              # Flask, Socket.IO, callbacks воркера
│   ├── routes.py            # Страницы, HTTP API и хранилище заданий в памяти
│   ├── ffmpeg_worker.py     # Очередь, параллельность, FFmpeg и финализация
│   ├── ffmpeg_bootstrap.py  # Пути бинарников и runtime-загрузка FFmpeg
│   ├── mkvmerge_remux.py    # Метаданные, флаги дорожек и remux MKV
│   ├── audio_downmix.py     # Выборочный stereo downmix
│   ├── internal_source.py   # Разрешённые пути и временные копии исходников
│   ├── file_utils.py        # Проверка расширений, имена, размеры и хеши
│   ├── settings_store.py    # Настройки в JSON
│   ├── job_store.py         # Задания и квитанции internal encode в SQLite
│   ├── logger.py            # Файловые и системные логи
│   ├── log_manager.py       # Логи в памяти для UI
│   ├── models/              # Job, статусы и слои пресетов
│   ├── templates/           # Jinja: загрузка, настройки, логи, система
│   ├── static/              # JavaScript и CSS без сборщика
│   └── assets/empty.ass     # Пустая дорожка субтитров для remux
├── presets/                 # YAML слои video / audio / subtitles / data
├── tests/                   # pytest: API, настройки, воркер, downmix, remux
├── docs/                    # Карта проекта, разработка и контракт API
├── docker/                  # Dockerfile и entrypoint
├── docker-compose*.yml      # Варианты запуска и хранения данных
├── scripts/                 # Сборка и публикация Docker образов
├── .github/workflows/       # Публикация образа в CI
├── requirements.txt         # Зависимости приложения
└── requirements-dev.txt     # pytest
```

## Быстрый старт

### 1. Клонирование и подготовка

```bash
git clone https://github.com/malinin1991/al-web-video-encoder.git
cd al-web-video-encoder
```

### 2. Запуск через Docker Compose

```bash
docker compose up --build
```

Система будет доступна по адресу: http://localhost:3000

### 3. Запуск на Windows

#### Способ 1: Простой запуск (рекомендуется)

1. **Скачайте проект** любым удобным способом
2. **Откройте папку** с проектом в проводнике
3. **Дважды кликните** на файл `start-windows.bat`
4. **Выберите действие** в появившемся меню
5. **Откройте браузер**: http://localhost:3000

#### Способ 2: Через командную строку

```cmd
# Клонирование (если есть Git)
git clone https://github.com/malinin1991/al-web-video-encoder.git
cd al-web-video-encoder

# Запуск
docker compose -f docker-compose.windows.yml up --build
```

#### Способ 3: Через PowerShell

```powershell
# Клонирование
git clone https://github.com/malinin1991/al-web-video-encoder.git
cd al-web-video-encoder

# Запуск
docker compose -f docker-compose.windows.yml up --build
```

📋 **Подробная инструкция**: См. файл [WINDOWS_SETUP.md](WINDOWS_SETUP.md)

### 4. Альтернативный запуск (без Docker)

```bash
# Окружение Python 3.15
python3.15 -m venv .venv
source .venv/bin/activate

# Установка зависимостей
python -m pip install -r requirements.txt

# Запуск сервера (после подготовки бинарников и каталогов данных)
python run.py
```

Локальный сервер слушает порт **5000**. Для обработки файлов нужны FFmpeg,
ffprobe и MKVToolNix. Для результатов можно задать доступный `OUTPUTS_DIR`
(по умолчанию `/app/outputs`).
Подготовка локального окружения и ограничения описаны в [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Основной runtime — обычный Python **3.15.0**. Free-threaded 3.15t также проверен;
для локального использования выбран стандартный вариант с меньшим расходом памяти
и более быстрым обслуживанием одного клиента. Методика и цифры сравнения —
в [разделе разработки](docs/DEVELOPMENT.md#сравнение-315--315t).

## Использование

### Загрузка файлов

1. Откройте главную страницу (http://localhost:3000 при запуске через Compose)
2. Выберите файл для загрузки:
   - Один или несколько видеофайлов (.mp4, .mkv)
   - Либо переключитесь на «С хранилища» и укажите разрешённый путь внутри контейнера
3. Настройте параметры кодирования FFmpeg
4. Нажмите «Загрузить» для файлов или «Кодировать» для хранилища

Параметры фиксируются при начале отправки и одинаковы для всей выбранной пачки.
При частичном успехе интерфейс показывает число принятых файлов и причины ошибок;
в выборе для повтора остаются только непринятые файлы. На 100% кодирования может
ещё идти финализация MKV: скачивание появляется после готовности итогового файла.

### Система пресетов

Видео и аудио выбираются независимо:

- **Видео**: `hevc_auto_level_rip` (HEVC rip auto level, по умолчанию), `hevc_rip`, `hevc_remux`
- **Аудио**: `copy` (по умолчанию), `opus` (160k VBR), `aac` (192k), `aac_fdk` (192k), `none`
- **Субтитры и данные**: внутренние слои `copy` / `none`; при аудио `none` также отключаются

`hevc_remux` — пресет перекодирования через libx265. Сборкой контейнера после
кодирования управляет отдельный модуль `mkvmerge_remux.py`.
У `hevc_auto_level_rip` x265 сам выбирает level/ref: параметры `level=5.1:ref=6`
не передаются. Этот default применяется и в UI, и при пропущенном `video_id` в API.

### Параметры кодирования

Система предоставляет полный контроль над параметрами FFmpeg:

- **Карта потоков**: Выбор видео, аудио, субтитров и данных
- **Кодек видео**: H.265 (HEVC) - единственный поддерживаемый кодек
- **Скорость кодирования**: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
- **Качество (CRF)**: От 0 (без потерь) до 51 (низкое качество)
- **Формат пикселей**: auto, 8-bit (yuv420p), 10-bit (yuv420p10le) или 10-bit 4:4:4 (yuv444p10le)
- **Оптимизация (x265 tune)**: none (не передаётся), animation, grain, psnr, ssim, fastdecode, zerolatency
- **Настройки аудио**: Битрейт и частота дискретизации для AAC/Opus

### Мониторинг заданий

- **Статусы**: Запланировано, Выполняется, Готово, Ошибка, Отменено
- **Прогресс**: Real-time обновление процента выполнения
- **Логи**: Live вывод FFmpeg в реальном времени
- **Действия**: Скачивание, остановка, удаление заданий

## API Endpoints

Полная спецификация (batch, пресеты, коды ответов, примеры curl): **[docs/API.md](docs/API.md)**.

### Основные маршруты

- `GET /` - Главная страница
- `GET /logs` - Страница логов
- `GET /settings` - Настройки параллельного кодирования
- `GET /systeminfo` - Состояние системы и медиабинарников
- `GET /health` - Проверка здоровья системы

### API для заданий

- `GET /api/jobs` - Список всех заданий
- `GET /api/jobs/<job_id>` - Конкретное задание
- `POST /api/upload` - Загрузка файла или пачки (`file` / `files` + `video_id` + `audio_id` + `audio_downmix`)
- `POST /api/internal/encode` - Кодирование с хранилища (`path` / `paths` + те же поля)
- `GET /api/internal/encode/requests/<request_id>` - Квитанция идемпотентного запроса
- `GET /api/jobs/<job_id>/download` - Скачивание результата
- `POST /api/jobs/download` - Пакетное скачивание готовых в ZIP (`ZIP_STORED`, имя `kensetsu-{blake2b8}.zip`)
- `DELETE /api/jobs/<job_id>/delete` - Удаление задания
- `POST /api/jobs/delete` - Пакетное удаление заданий
- `POST /api/jobs/<job_id>/stop` - Остановка задания

Параллельное кодирование: на странице **Настройки** (`/settings`) или через `GET`/`PUT /api/settings` (`max_concurrent_encodes`, 1–16, по умолчанию **1**). Env `MAX_CONCURRENT_ENCODES` — только fallback, пока значение не сохранено в `settings/app_settings.json`. Изменение применяется без рестарта к следующим слотам. Два полных x265 могут конкурировать за ядра — при необходимости снизьте `cpu_threads` / `x265_pools` в пресете.

### API для настроек

- `GET /api/settings` — текущий лимит параллельности и границы
- `PUT /api/settings` — `{ "max_concurrent_encodes": N }` (1–16), применяется без рестарта
- `GET`/`PUT /api/settings/metadata` — ник для метаданных дорожек

### API для пресетов

- `GET /api/presets` - `{ videos, audios, defaults }` (`available` у audio)
- `GET /api/presets/resolve` - слитые `ffmpeg_params` (`?video_id=&audio_id=`)

### Слои пресетов

| Слой | ids | UI |
|------|-----|----|
| video | `hevc_auto_level_rip` (default), `hevc_rip`, `hevc_remux` | да |
| audio | `copy` (default), `opus`, `aac`, `aac_fdk`, `none` | да |
| subtitles | `copy` (default), `none` | нет |
| data | `copy` (default), `none` | нет |

`audio_downmix`: `none` \| `stereo` — selective downmix только для дорожек с >2 каналами (ATSC LoRo pan без LFE).  
При `audio_id=none` автоматически `subtitles=none` и `data=none`.

В каждом слое ровно один пресет с `default: true`.

### WebSocket события

- `job_update` - Обновление статуса задания
- `progress_update` - Обновление прогресса
- `log_update` - Новые логи

Клиент Socket.IO поставляется с приложением. Статус подключения виден в навигации;
при потере соединения задания и логи обновляются через HTTP каждые 5 секунд
(когда вкладка видима). История загружается сразу, без ожидания live-подключения.

## Создание собственных пресетов

### Добавление video / audio пресета

1. Создайте файл в `presets/video/` или `presets/audio/`
2. Скопируйте структуру соседнего YAML; укажите `default: true` только у одного пресета слоя
3. Перезапустите приложение

Subtitles/data правятся в `presets/subtitles/` и `presets/data/` (в UI пока не выводятся).

### Пример video-пресета

```yaml
id: hevc_auto_level_rip
name: HEVC rip auto level
description: WEBRip / HDTV / BDRip — libx265 auto level CRF 23 slow
default: true

video_codec: libx265
preset: slow
crf: "23"
pixel_format: yuv420p10le
tune: animation
output_format: matroska
video_map: 0:v:0
```

### Пример audio-пресета

```yaml
id: opus
name: Opus
description: Opus VBR 160k
default: false

codec: libopus
bitrate: 160k
sample_rate: "48000"
vbr: true
audio_map: 0:a?
```

### Stereo downmix

Опция `audio_downmix=stereo` (только с re-encode аудио): по ffprobe для каждой дорожки с `channels > 2` добавляется ATSC LoRo `pan` без LFE; stereo/mono не трогаются. Ошибка probe → задание падает с ошибкой.

Подробности по HTTP API и сценариям batch — в `docs/API.md`.

## Конфигурация

### Переменные окружения

- `FLASK_ENV` - Режим Flask (development/production)
- `PYTHONPATH` - Путь к модулям Python
- `UPLOADS_DIR` / `OUTPUTS_DIR` - Каталоги исходников/результатов (default `/app/uploads` / `/app/outputs`)
- `CORS_ORIGINS` - Разрешённые origins для CORS / Socket.IO (через запятую)
- `SOCKETIO_ASYNC_MODE` - Режим Flask-SocketIO (`threading` по умолчанию; без eventlet)
- `FFMPEG_DIR` - Каталог FFmpeg (по умолчанию `/app/ffmpeg`; бинарники в `$FFMPEG_DIR/bin`)
- `FFMPEG_BIN` / `FFPROBE_BIN` - Абсолютные пути к своим бинарникам (опционально)
- `FFMPEG_DOWNLOAD_URL` - Полный URL архива вместо BtbN latest (опционально)
- `FFMPEG_SKIP_DOWNLOAD=1` - Не скачивать FFmpeg автоматически (ошибка, если бинарников нет)

### FFmpeg в Docker (runtime)

FFmpeg **не встраивается в образ** при `docker build`. При первом старте контейнера entrypoint скачивает BtbN FFmpeg n9.0 GPL (amd64/arm64) в `/app/ffmpeg/bin`, если `ffmpeg` и `ffprobe` ещё отсутствуют.

- Том `ffmpeg_bin` сохраняет бинарники между перезапусками.
- Свои бинарники: положите `bin/ffmpeg` и `bin/ffprobe` в каталог на хосте и смонтируйте его в `/app/ffmpeg` (см. комментарии в `docker-compose*.yml`). При наличии исполняемых файлов загрузка пропускается.
- Для пресетов **AAC FDK** (`libfdk_aac`) нужна nonfree-сборка FFmpeg с FDK: смонтируйте её в `/app/ffmpeg` или задайте `FFMPEG_BIN` / `FFPROBE_BIN`.
- Первый старт может занять дольше (скачивание); `healthcheck.start_period` увеличен до 120s.

### MKVToolNix (mkvmerge)

В образе ставится **mkvtoolnix** (только CLI) из официального APT-репозитория [mkvtoolnix.download](https://mkvtoolnix.download/), а не из пакетов дистрибутива Debian. Версия CLI следует upstream Moritz Bunkus.

### Docker Volumes

- `uploads` - Загруженные файлы
- `outputs` - Закодированные файлы
- `logs` - Логи системы
- `settings` - Настройки (`app_settings.json`) и SQLite заданий/квитанций (`jobs.sqlite3`)
- `data` - Дополнительный каталог данных (`/app/data`)
- `ffmpeg_bin` - Бинарники FFmpeg (`/app/ffmpeg`)

### Хранение на диске хоста

По умолчанию данные хранятся в Docker named volumes и не видны напрямую в файловой системе хоста. Чтобы вынести загрузки, результаты кодирования и логи на физический диск:

1. **Создайте каталоги на хосте** (в корне проекта):
   ```bash
   mkdir -p data/uploads data/outputs data/logs
   ```
   На Windows (PowerShell):
   ```powershell
   New-Item -ItemType Directory -Force -Path data\uploads, data\outputs, data\logs
   ```

2. **Отредактируйте** `docker-compose.yml` (или `docker-compose.windows.yml` на Windows):
   - Закомментируйте строки named volumes (`uploads:`, `outputs:`, `logs:`)
   - Раскомментируйте bind-mount:
     ```yaml
     - ./data/uploads:/app/uploads
     - ./data/outputs:/app/outputs
     - ./data/logs:/app/logs
     ```

3. **Перезапустите контейнер**:
   ```bash
   docker compose down
   docker compose up --build
   ```
   На Windows:
   ```powershell
   docker compose -f docker-compose.windows.yml down
   docker compose -f docker-compose.windows.yml up --build
   ```

После этого файлы будут доступны в `./data/` на хосте. История заданий и квитанции
сохраняются в `/app/settings/jobs.sqlite3` (`JOB_STORE_PATH`) и восстанавливаются
после перезапуска. Незавершённые задания получают `error` с причиной прерывания,
автоматического повторного запуска нет. Сохраняйте том `settings` вместе с outputs;
при потере SQLite теряется история идемпотентности. Контракт `input_path`,
доступности результата и `request_id` описан в [docs/API.md](docs/API.md).

## Сборка и публикация образа в приватный registry

Проект поддерживает сборку Docker-образа и публикацию в приватное хранилище образов (Harbor, GitLab Registry, GHCR и т.д.).

### 1. Настройка переменных

```powershell
# Windows
copy registry.example.env registry.env
notepad registry.env
```

```bash
# Linux / macOS
cp registry.example.env registry.env
$EDITOR registry.env
```

Укажите в `registry.env`:

| Переменная | Пример | Описание |
|------------|--------|----------|
| `REGISTRY` | `registry.mageek.su` | URL registry без `/` в конце |
| `IMAGE_NAME` | `al-web-video-encoder` | Имя репозитория образа |
| `IMAGE_TAG` | `latest` или `1.0.0` | Тег версии |

Скрипты `build-and-push` автоматически передают `BUILD_TIME` (UTC ISO) и `GIT_SHA` (короткий commit) как build-args; в образ записываются OCI-метки `org.opencontainers.image.created` и `org.opencontainers.image.revision`.

Опционально для автоматического входа: `REGISTRY_USER`, `REGISTRY_PASSWORD`.

### 2. Вход в registry

```powershell
docker login registry.mageek.su
```

### 3. Сборка и push

**Windows (PowerShell) — быстрый multi-arch push:**

```powershell
.\scripts\publish.ps1
```

**Windows (PowerShell) — полный скрипт:**

```powershell
.\scripts\build-and-push.ps1
# только сборка:
.\scripts\build-and-push.ps1 -BuildOnly
# мультиарх (amd64 + arm64/v8):
.\scripts\build-and-push.ps1 -MultiArch
```

**Linux / macOS:**

```bash
chmod +x scripts/build-and-push.sh
./scripts/build-and-push.sh
./scripts/build-and-push.sh --multiarch
```

**Вручную multi-arch (PowerShell, без скриптов):**

```powershell
docker buildx build `
  --builder=container `
  --platform=linux/amd64,linux/arm64/v8 `
  --provenance=false `
  --sbom=false `
  --build-arg BUILD_TIME="$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" `
  --build-arg GIT_SHA="$(git rev-parse --short HEAD)" `
  -f docker/Dockerfile `
  -t registry.mageek.su/al-web-video-encoder:latest `
  --push `
  .
```

### 4. Запуск образа из registry (без локальной сборки)

На Linux-сервере:

```bash
docker login registry.mageek.su
docker compose --env-file registry.env -f docker-compose.registry.yml pull
docker compose --env-file registry.env -f docker-compose.registry.yml up -d
```

На Windows (без монтирования `/etc/localtime`):

```powershell
docker login registry.mageek.su
docker compose --env-file registry.env -f docker-compose.registry.windows.yml pull
docker compose --env-file registry.env -f docker-compose.registry.windows.yml up -d
```

Файлы `docker-compose.registry.yml` и `docker-compose.registry.windows.yml` повторяют настройки продакшена, но используют готовый образ из registry вместо локальной сборки.

### 5. CI/CD (GitHub Actions)

В репозитории подготовлен workflow `.github/workflows/docker-publish.yml`. Он запускается при push тега `v*` или вручную.

**Секреты репозитория:**

- `REGISTRY` — URL registry
- `REGISTRY_USER` — пользователь
- `REGISTRY_PASSWORD` — токен или пароль

**Переменная репозитория (опционально):** `IMAGE_NAME` (по умолчанию `al-web-video-encoder`).

> Перед первым запуском workflow задайте секреты репозитория (`REGISTRY`, `REGISTRY_USER`, `REGISTRY_PASSWORD`) в Settings → Secrets and variables → Actions.

Подробнее о мультиарх-сборке: [MULTIARCH.md](MULTIARCH.md).

## Требования к системе

### Минимальные требования

- Docker и Docker Compose
- 4 GB RAM
- 10 GB свободного места
- Поддержка x86_64 архитектуры

### Рекомендуемые требования

- 8 GB RAM
- 50 GB свободного места
- SSD накопитель
- Многоядерный процессор

## Устранение неполадок

### Проблемы с Docker

```bash
# Очистка Docker кэша
docker system prune -a

# Пересборка образов
docker-compose build --no-cache

# Просмотр логов
docker-compose logs -f
```

### Проблемы с FFmpeg

```bash
# Проверка установки FFmpeg (после первого старта / тома)
docker-compose exec video-encoder ffmpeg -version

# Проверка доступных кодеков
docker-compose exec video-encoder ffmpeg -codecs | grep x265

# Логи bootstrap при старте
docker-compose logs video-encoder | grep ffmpeg-bootstrap
```

Если автозагрузка недоступна (нет сети), смонтируйте свои Linux-бинарники в `/app/ffmpeg` или задайте `FFMPEG_BIN` / `FFPROBE_BIN`.

### Ошибки NUMA (можно игнорировать)

При кодировании видео вы можете увидеть ошибки вида:
```
x265 [error]: unable to set thread affinity for NUMA node mask
```

**Эти ошибки можно безопасно игнорировать.** Они возникают из-за ограничений Docker контейнеров в управлении NUMA узлами и не влияют на качество или скорость кодирования. x265 продолжит работу без NUMA оптимизации, что является нормальным поведением в контейнеризованной среде.

**Причины возникновения:**
- Docker контейнеры не имеют полного доступа к NUMA узлам хост-системы
- x265 пытается использовать NUMA оптимизацию, но не может получить необходимые разрешения
- Это стандартная ситуация для большинства Docker-окружений

**Рекомендации:**
- Игнорируйте эти сообщения - они не критичны
- Кодирование будет работать с полной производительностью
- Для полного устранения потребовалась бы пересборка x265 без поддержки NUMA

### Проблемы с памятью

- Увеличьте лимит памяти Docker
- Используйте более быстрый preset для кодирования
- Обрабатывайте файлы меньшего размера

## Безопасность

- Загружаемые файлы проверяются по расширению `.mp4` / `.mkv`
- Временные копии исходников удаляются после успешного кодирования; исходники хранилища сохраняются
- Разрешённые origins HTTP и Socket.IO задаются через `CORS_ORIGINS`; авторизации в API сейчас нет
- Ограничение размера загружаемых файлов (16GB)

## Лицензия

MIT License

## Поддержка

Для получения помощи создайте issue в репозитории или обратитесь к документации FFmpeg.
