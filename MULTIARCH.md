# Мультиплатформенная поддержка

Этот проект поддерживает сборку и запуск на различных архитектурах процессоров.

## Поддерживаемые архитектуры

- **linux/amd64** — Intel/AMD 64-bit (x86_64)
- **linux/arm64/v8** — ARM 64-bit (Apple M1/M2/M3, современные ARM серверы)

**linux/arm/v7 не поддерживается:** у [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) для ветки n9.0 нет сборок armhf. Скрипты публикации и Dockerfile рассчитаны только на amd64 + arm64.

## FFmpeg 9.x (runtime, не при сборке)

FFmpeg **не скачивается во время `docker build`**. Образ лёгкий; при первом запуске контейнера `docker/entrypoint.sh` вызывает `app/ffmpeg_bootstrap.py`, который при отсутствии бинарников загружает BtbN FFmpeg-Builds (GPL) под архитектуру **рантайма** (`platform.machine()`):

| Архитектура контейнера | Архив |
|------------------------|--------|
| x86_64 / amd64 | `ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz` |
| aarch64 / arm64 | `ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz` |

Базовый URL: `https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/`

- Установка: `$FFMPEG_DIR/bin/ffmpeg` и `ffprobe` (по умолчанию `/app/ffmpeg/bin`)
- Том Compose `ffmpeg_bin` → `/app/ffmpeg` сохраняет загрузку между рестартами
- Переопределение: bind-mount своих бинарников или `FFMPEG_BIN` / `FFPROBE_BIN` / `FFMPEG_DOWNLOAD_URL`
- `FFMPEG_SKIP_DOWNLOAD=1` — только пользовательские бинарники, без сети

Мультиарх-сборка образа больше не тянет разные tarball по `TARGETARCH` на этапе build — загрузка происходит уже в контейнере нужной платформы.

## MKVToolNix

Пакет `mkvtoolnix` (CLI `mkvmerge`) ставится в Dockerfile из официального репозитория **mkvtoolnix.download** (ключ Moritz Bunkus AS-IS в `/etc/apt/keyrings/`, suite **trixie** — как у `python:3.15.0-slim-trixie`), а не из зеркала Debian. Версия mkvmerge в контейнере — upstream, не дистро-пакет.

## Релиз сборки 61

`registry.mageek.su/al-web-video-encoder:build-61` содержит стандартный Python 3.15.0
для `linux/amd64` и `linux/arm64`. Тег `latest` указывает на тот же multiarch manifest
после проверки обеих платформ. Номер сборки хранится в `build_number.txt`,
commit исходников — в OCI label `org.opencontainers.image.revision`.

## Сборка для разных платформ

### Локальная сборка (рекомендуется)
```bash
# Сборка для текущей архитектуры
make build-local

# Или напрямую
docker-compose build --no-cache
```

### Мультиплатформенная сборка
```bash
# Сборка для amd64 + arm64 (через Makefile)
make build-multiarch

# Публикация в registry (рекомендуется)
./scripts/build-and-push.sh --multiarch

# Или вручную (Linux/macOS)
docker buildx build --builder=container \
  --platform linux/amd64,linux/arm64/v8 \
  --provenance=false --sbom=false \
  -f docker/Dockerfile \
  --build-arg BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  --build-arg GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  -t registry.mageek.su/al-web-video-encoder:latest \
  --push .
```

# Windows PowerShell (или просто .\scripts\publish.ps1)
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish.ps1
```

### Сборка для конкретной архитектуры
```bash
# Для Apple Silicon (M1/M2/M3)
docker buildx build --platform linux/arm64 -t video-encoder:arm64 .

# Для Intel/AMD
docker buildx build --platform linux/amd64 -t video-encoder:amd64 .
```

## Запуск

### Обычный запуск
```bash
make up
# или
docker-compose up -d
```

### Мультиплатформенный запуск
```bash
docker-compose -f docker-compose.multiarch.yml up -d
```

## Проверка архитектуры

### В контейнере
```bash
# Подключиться к контейнеру
make shell

# Проверить архитектуру
uname -m

# Проверить версию FFmpeg
ffmpeg -version
```

### На хосте
```bash
# Проверить архитектуру хоста
uname -m

# Проверить поддерживаемые платформы Docker
docker buildx ls
```

## Производительность по архитектурам

| Архитектура | Производительность | Рекомендации |
|-------------|-------------------|--------------|
| **amd64** | Высокая | Лучший выбор для серверов |
| **arm64** | Высокая | Отлично для Apple Silicon |

## Устранение неполадок

### Ошибка "Unsupported architecture" / arm/v7
Если bootstrap падает на неподдерживаемой архитектуре — у BtbN для FFmpeg 9 нет armhf. Используйте amd64 или arm64, либо смонтируйте свои бинарники.

Проверьте архитектуру хоста:
```bash
uname -m
```

### Медленная сборка на ARM
ARM процессоры могут собирать образы медленнее. Используйте:
```bash
# Сборка только для текущей архитектуры
make build-local
```

### Проблемы с Docker Buildx
Если buildx не установлен:
```bash
# Установка buildx
docker buildx install
```

## Примеры использования

### На MacBook M1/M2/M3
```bash
make build-local
make up
```

### На сервере Intel/AMD
```bash
make build-local
make up
```

### Для распространения
```bash
# Через скрипт (рекомендуется) — передаёт BUILD_TIME, GIT_SHA, push в registry
./scripts/build-and-push.sh --multiarch

# Windows PowerShell
.\scripts\build-and-push.ps1 -MultiArch
```

Переменные registry задаются в `registry.example.env` → `registry.env`. См. раздел README «Сборка и публикация образа в приватный registry».
