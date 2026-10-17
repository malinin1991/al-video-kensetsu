# 🚀 Установка на Windows с Docker Desktop

## Предварительные требования

- ✅ Docker Desktop для Windows установлен и запущен
- ✅ Git (опционально, для клонирования репозитория)

## Быстрый старт

При переходе с версии без редактора сначала выполните
[резервирование старой установки](#первый-переход-на-редактор-пресетов).
Оно требуется **до первого обновления Git/образа или пересоздания контейнера**.

### 1. Получение кода

**Способ A: Git (рекомендуется)**
```bash
git clone https://github.com/malinin1991/al-video-kensetsu.git
cd al-video-kensetsu
```

**Способ B: Архив**
1. Скачайте архив проекта
2. Распакуйте в папку `al-video-kensetsu`
3. Откройте PowerShell в этой папке

### 2. Запуск приложения

```bash
# Используйте Windows-совместимый compose файл
docker compose -f docker-compose.windows.yml up --build
```

### 3. Доступ к приложению

Откройте браузер и перейдите по адресу:
- **http://localhost:3000** - главная страница
- **http://localhost:3000/logs** - страница логов
- **http://localhost:3000/presets** - редактор video/audio/subtitles/data
- **http://localhost:3000/systeminfo** - информация о системе

## Управление приложением

### Остановка
```bash
docker compose -f docker-compose.windows.yml down
```

### Остановка с удалением данных

Следующая команда удаляет named volumes, включая настройки, историю и
пользовательские пресеты. Для обычного обновления используйте остановку без `-v`.

```bash
docker compose -f docker-compose.windows.yml down -v
```

### Просмотр логов
```bash
docker compose -f docker-compose.windows.yml logs -f
```

### Пересборка после изменений
```bash
docker compose -f docker-compose.windows.yml up --build --force-recreate
```

Пользовательские изменения `/presets` хранятся в
`/app/settings/presets/catalog.json` (`USER_PRESETS_PATH`) и сохраняются при
пересборке с прежним томом settings. Встроенные YAML в `app/builtin_presets/`
приложение не изменяет; старый `./presets` больше не нужен как runtime mount.
При смене имени Compose-проекта подключите прежний том явно.

## Первый переход на редактор пресетов

Дождитесь завершения заданий. В PowerShell **до обновления исходников/образа**
сохраните старые YAML и settings из ещё существующего контейнера во внешнюю папку:

```powershell
$presetBackupPath = Join-Path $env:USERPROFILE 'al-video-kensetsu-before-preset-editor'
New-Item -ItemType Directory -Force -Path $presetBackupPath
docker compose -f docker-compose.windows.yml stop video-encoder
docker compose -f docker-compose.windows.yml cp video-encoder:/app/presets "$presetBackupPath/presets"
docker compose -f docker-compose.windows.yml cp video-encoder:/app/settings "$presetBackupPath/settings"
Get-ChildItem "$presetBackupPath/presets", "$presetBackupPath/settings"
```

Используйте новую резервную папку, проверьте содержимое и сохраните текущий
commit/тег образа. Если раньше запускали Python напрямую, скопируйте фактические
`presets` и `settings` через `Copy-Item -Recurse` в ту же внешнюю папку.
Только после сохранения копий обновите Git/образ и соберите новую версию.
Сервер оставьте остановленным, выполните preview и затем apply:

```powershell
docker compose -f docker-compose.windows.yml build video-encoder
docker compose -f docker-compose.windows.yml run --rm --no-deps --entrypoint python `
  -v "${presetBackupPath}/presets:/legacy-presets:ro" video-encoder `
  -m app.preset_migration preview --source /legacy-presets
docker compose -f docker-compose.windows.yml run --rm --no-deps --entrypoint python `
  -v "${presetBackupPath}/presets:/legacy-presets:ro" video-encoder `
  -m app.preset_migration apply --source /legacy-presets `
  --preview-digest 'PREVIEW_DIGEST' --if-match 'CATALOG_REVISION'
```

Подставьте значения `preview_digest` и `catalog_revision` из просмотра.
При ошибках/конфликтах ничего не перезаписывается; исправьте причину и повторите
preview. Используется прежний том settings. Для registry варианта указывайте
его Compose и `--env-file`, вместо build выполните pull нового образа.
После успешного переноса запустите сервер и проверьте `/presets`.

При повреждении пользовательского JSON остановите сервер. Для offline restore
сначала получите SHA256 текущего файла, затем укажите проверенную копию:

```powershell
docker compose -f docker-compose.windows.yml run --rm --no-deps --entrypoint sha256sum video-encoder /app/settings/presets/catalog.json
docker compose -f docker-compose.windows.yml run --rm --no-deps --entrypoint python video-encoder `
  -m app.preset_migration restore `
  --backup /app/settings/presets/backups/BACKUP.json `
  --target-digest 'SHA256_CURRENT_FILE'
```

Для прямого Python задайте `$env:USER_PRESETS_PATH` абсолютным путём,
используйте `Get-FileHash -Algorithm SHA256` и ту же команду модуля.
Restore сохраняет оригинал в recovery и создаёт новую revision; приложение
не сбрасывает повреждённый каталог в пустой. Полные правила и ограничения:
[DEVELOPMENT.md](docs/DEVELOPMENT.md#перенос-старых-пресетов-и-восстановление).

## Возможные проблемы

### 1. Порт 3000 занят
Если порт 3000 занят, измените в `docker-compose.windows.yml`:
```yaml
ports:
  - "3001:5000"  # Используйте другой порт
```

### 2. Проблемы с правами доступа
В Docker Desktop:
1. Откройте Settings
2. Перейдите в Resources → File Sharing
3. Убедитесь, что диск с проектом доступен для Docker

### 3. Медленная работа
- Увеличьте количество CPU и памяти для Docker в настройках
- Убедитесь, что WSL2 включен (если используется)

## Тестирование

1. **Загрузка файла**: Перетащите видео файл в область загрузки
2. **Выбор пресета**: Попробуйте разные пресеты из выпадающего списка
3. **Проверка логов**: Перейдите на страницу логов и проверьте отображение
4. **Настройки производительности**: Проверьте автоматическое определение потоков

## Структура проекта

```
al-video-kensetsu/
├── app/                    # Код приложения
│   └── builtin_presets/    # Поставляемые read-only YAML
├── docker/                # Docker конфигурация
├── docker-compose.windows.yml  # Windows compose файл
└── WINDOWS_SETUP.md       # Эта инструкция
```

## Поддержка

При возникновении проблем:
1. Проверьте логи: `docker compose -f docker-compose.windows.yml logs`
2. Убедитесь, что Docker Desktop запущен
3. Проверьте, что порт 3000 свободен
