# Система пакетного кодирования видео

Веб-система для пакетного кодирования видео с использованием Python 3.14, Flask, Bootstrap, Docker и FFmpeg.

## Возможности

- **Загрузка файлов**: Поддержка одиночных видео файлов (.mp4, .mkv) и ZIP архивов
- **Система пресетов**: Готовые настройки кодирования для различных сценариев
- **Real-time обновления**: Прогресс кодирования и логи обновляются в реальном времени через WebSocket
- **Настраиваемые параметры**: Полная настройка параметров FFmpeg через веб-интерфейс
- **Расширенные настройки аудио**: Битрейт и частота дискретизации для AAC/Opus
- **Управление заданиями**: Просмотр, остановка, скачивание и удаление заданий
- **Автоматическая очистка**: Исходные файлы удаляются после успешного кодирования

## Технологии

- **Backend**: Python 3.14, Flask, Flask-SocketIO
- **Frontend**: Bootstrap 5, JavaScript, WebSocket
- **Кодирование**: FFmpeg с поддержкой H.265/HEVC
- **Конфигурация**: YAML пресеты для удобного управления настройками
- **Контейнеризация**: Docker, Docker Compose
- **Real-time**: WebSocket для live обновлений

## Структура проекта

```
project/
├── app/
│   ├── main.py              # Основное Flask приложение
│   ├── routes.py            # API маршруты
│   ├── ffmpeg_worker.py     # Воркер для кодирования видео
│   ├── ffmpeg_bootstrap.py  # Runtime-загрузка / пути FFmpeg
│   ├── file_utils.py        # Утилиты для работы с файлами
│   ├── models/
│   │   ├── job.py           # Модель задания
│   │   └── presets.py       # Система пресетов
│   ├── static/js/
│   │   ├── upload.js        # JavaScript для загрузки
│   │   └── logs.js          # JavaScript для логов
│   └── templates/
│       ├── base.html        # Базовый шаблон
│       ├── index.html       # Главная страница
│       └── logs.html        # Страница логов
├── presets/                 # YAML пресеты по слоям
│   ├── video/               # hevc_rip (default), hevc_remux
│   ├── audio/               # copy (default), opus, aac, aac_fdk, none
│   ├── subtitles/           # copy (default), none — не в UI
│   └── data/                # copy (default), none — не в UI
├── docs/
│   └── API.md               # Спецификация HTTP API для интеграций
├── docker/
│   ├── Dockerfile           # Docker образ (без FFmpeg в build; MKVToolNix из официального APT)
│   └── entrypoint.sh        # PATH + bootstrap FFmpeg
├── docker-compose.yml       # Docker Compose конфигурация
├── requirements.txt         # Python зависимости
└── README.md               # Документация
```

## Быстрый старт

### 1. Клонирование и подготовка

```bash
git clone https://github.com/malinin1991/al-web-video-encoder.git
cd al-web-video-encoder
```

### 2. Запуск через Docker Compose

#### Автоматический запуск (рекомендуется)
```bash
./run.py
```

#### Ручной запуск
```bash
docker-compose up --build
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
# Установка зависимостей
pip install -r requirements.txt

# Установка FFmpeg (Ubuntu/Debian)
sudo apt update
sudo apt install ffmpeg

# Запуск приложения
python -m app.main
```

## Использование

### Загрузка файлов

1. Откройте главную страницу (http://localhost:5000)
2. Выберите файл для загрузки:
   - Одиночный видео файл (.mp4, .mkv)
   - ZIP архив с видео файлами
3. Настройте параметры кодирования FFmpeg
4. Нажмите "Загрузить"

### Система пресетов

Система включает готовые пресеты для различных сценариев кодирования:

- **По умолчанию** - Стандартные настройки HEVC с копированием аудио
- **HEVC + AAC** - HEVC видео с AAC аудио (128k, 48kHz)
- **HEVC + Opus** - HEVC видео с Opus аудио (128k, 48kHz)
- **Высокое качество** - Максимальное качество для архивирования
- **Быстрое кодирование** - Быстрое кодирование с приемлемым качеством
- **Оптимизация для аниме** - Специальные настройки для аниме и мультфильмов

### Параметры кодирования

Система предоставляет полный контроль над параметрами FFmpeg:

- **Карта потоков**: Выбор видео, аудио, субтитров и данных
- **Кодек видео**: H.265 (HEVC) - единственный поддерживаемый кодек
- **Скорость кодирования**: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
- **Качество (CRF)**: От 0 (без потерь) до 51 (низкое качество)
- **Формат пикселей**: 8-bit (yuv420p) или 10-bit (yuv420p10le) цвет
- **Оптимизация (x265 tune)**: none (не передаётся), animation, grain, psnr, ssim, fastdecode, zerolatency
- **Настройки аудио**: Битрейт и частота дискретизации для AAC/Opus

### Мониторинг заданий

- **Статусы**: Запланировано (серый), Выполняется (желтый), Готово (зеленый), Ошибка (красный)
- **Прогресс**: Real-time обновление процента выполнения
- **Логи**: Live вывод FFmpeg в реальном времени
- **Действия**: Скачивание, остановка, удаление заданий

## API Endpoints

Полная спецификация (batch, пресеты, коды ответов, примеры curl): **[docs/API.md](docs/API.md)**.

### Основные маршруты

- `GET /` - Главная страница
- `GET /logs` - Страница логов
- `GET /health` - Проверка здоровья системы

### API для заданий

- `GET /api/jobs` - Список всех заданий
- `GET /api/jobs/<job_id>` - Конкретное задание
- `POST /api/upload` - Загрузка файла или пачки (`file` / `files` + `video_id` + `audio_id` + `audio_downmix`)
- `POST /api/internal/encode` - Кодирование с хранилища (`path` / `paths` + те же поля)
- `GET /api/jobs/<job_id>/download` - Скачивание результата
- `DELETE /api/jobs/<job_id>/delete` - Удаление задания
- `POST /api/jobs/<job_id>/stop` - Остановка задания

### API для пресетов

- `GET /api/presets` - `{ videos, audios, defaults }` (`available` у audio)
- `GET /api/presets/resolve` - слитые `ffmpeg_params` (`?video_id=&audio_id=`)

### Слои пресетов

| Слой | ids | UI |
|------|-----|----|
| video | `hevc_rip` (default), `hevc_remux` | да |
| audio | `copy` (default), `opus`, `aac`, `aac_fdk`, `none` | да |
| subtitles | `copy` (default), `none` | нет |
| data | `copy` (default), `none` | нет |

`audio_downmix`: `none` \| `stereo` — selective downmix только для дорожек с >2 каналами (ATSC LoRo pan без LFE).  
При `audio_id=none` автоматически `subtitles=none` и `data=none`.

В каждом YAML ровно один пресет слоя с `default: true`.

### WebSocket события

- `job_update` - Обновление статуса задания
- `progress_update` - Обновление прогресса
- `log_update` - Новые логи

## Создание собственных пресетов

### Добавление video / audio пресета

1. Создайте файл в `presets/video/` или `presets/audio/`
2. Скопируйте структуру соседнего YAML; укажите `default: true` только у одного пресета слоя
3. Перезапустите приложение

Subtitles/data правятся в `presets/subtitles/` и `presets/data/` (в UI пока не выводятся).

### Пример video-пресета

```yaml
id: hevc_rip
name: HEVC rip
description: WEBRip / HDTV / BDRip — libx265 CRF 23 slow
default: true

video_codec: libx265
preset: slow
crf: "23"
pixel_format: yuv420p10le
x265_params: level=5.1:ref=6
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

После этого файлы будут доступны в `./data/` на хосте. Список заданий в интерфейсе хранится в памяти и сбрасывается при перезапуске контейнера, но закодированные файлы на диске сохраняются.

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

- Все загруженные файлы проверяются на тип
- Исходные файлы автоматически удаляются после кодирования
- WebSocket соединения защищены от CSRF
- Ограничение размера загружаемых файлов (16GB)

## Лицензия

MIT License

## Поддержка

Для получения помощи создайте issue в репозитории или обратитесь к документации FFmpeg.
