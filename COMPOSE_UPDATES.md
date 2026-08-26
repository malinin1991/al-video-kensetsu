# Обновления Docker Compose файлов

## Внесенные изменения

Во все Docker Compose файлы добавлены следующие переменные окружения для исправления проблем с Socket.IO:

### Добавленные переменные:
- `CORS_ORIGINS` - разрешенные источники для CORS
- `SOCKETIO_ASYNC_MODE=threading` - режим асинхронности для Socket.IO

### Обновленные файлы:

1. **docker-compose.yml** (основной)
   - `CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://172.18.0.1:3000`

2. **docker-compose.windows.yml** (Windows)
   - `CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://172.18.0.1:3000`

3. **docker-compose.dev.yml** (разработка)
   - `CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://172.18.0.1:3000`

4. **docker-compose.prod.yml** (продакшн)
   - `CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://172.18.0.1:3000`

5. **docker-compose.test.yml** (тестирование)
   - `CORS_ORIGINS=http://localhost:5001,http://127.0.0.1:5001,http://172.18.0.1:5001`
   - Порт 5001 для тестирования

## Использование

### Основной режим:
```bash
docker-compose up --build
```

### Режим разработки:
```bash
docker-compose -f docker-compose.dev.yml up --build
```

### Режим продакшн:
```bash
docker-compose -f docker-compose.prod.yml up --build
```

### Тестирование:
```bash
docker-compose -f docker-compose.test.yml up --build
```

### Windows:
```bash
docker-compose -f docker-compose.windows.yml up --build
```

## Результат

Эти изменения должны устранить ошибки 400 при подключении WebSocket во всех режимах работы приложения.

