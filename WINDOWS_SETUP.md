# 🚀 Установка на Windows с Docker Desktop

## Предварительные требования

- ✅ Docker Desktop для Windows установлен и запущен
- ✅ Git (опционально, для клонирования репозитория)

## Быстрый старт

### 1. Получение кода

**Способ A: Git (рекомендуется)**
```bash
git clone https://github.com/malinin1991/al-web-video-encoder.git
cd al-web-video-encoder
```

**Способ B: Архив**
1. Скачайте архив проекта
2. Распакуйте в папку `al_web_video_encoder`
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
- **http://localhost:3000/systeminfo** - информация о системе

## Управление приложением

### Остановка
```bash
docker compose -f docker-compose.windows.yml down
```

### Остановка с удалением данных
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
al_web_video_encoder/
├── app/                    # Код приложения
├── presets/               # Пресеты кодирования
├── docker/                # Docker конфигурация
├── docker-compose.windows.yml  # Windows compose файл
└── WINDOWS_SETUP.md       # Эта инструкция
```

## Поддержка

При возникновении проблем:
1. Проверьте логи: `docker compose -f docker-compose.windows.yml logs`
2. Убедитесь, что Docker Desktop запущен
3. Проверьте, что порт 3000 свободен
