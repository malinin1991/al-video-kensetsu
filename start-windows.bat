@echo off
echo ========================================
echo   Video Encoder - Windows Launcher
echo ========================================
echo.

REM Проверяем, установлен ли Docker
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ОШИБКА: Docker не найден!
    echo Установите Docker Desktop для Windows
    echo https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

echo Docker найден: 
docker --version

echo.
echo Выберите действие:
echo 1. Запустить приложение
echo 2. Запустить с пересборкой
echo 3. Остановить приложение
echo 4. Показать логи
echo 5. Очистить все данные
echo 6. Выйти
echo.

set /p choice="Введите номер (1-6): "

if "%choice%"=="1" (
    echo Запускаем приложение...
    docker compose -f docker-compose.windows.yml up -d
    echo.
    echo Приложение запущено!
    echo Откройте браузер: http://localhost:3000
    pause
) else if "%choice%"=="2" (
    echo Запускаем с пересборкой...
    docker compose -f docker-compose.windows.yml up --build -d
    echo.
    echo Приложение пересобрано и запущено!
    echo Откройте браузер: http://localhost:3000
    pause
) else if "%choice%"=="3" (
    echo Останавливаем приложение...
    docker compose -f docker-compose.windows.yml down
    echo Приложение остановлено.
    pause
) else if "%choice%"=="4" (
    echo Показываем логи (Ctrl+C для выхода)...
    docker compose -f docker-compose.windows.yml logs -f
) else if "%choice%"=="5" (
    echo ВНИМАНИЕ: Это удалит все данные!
    set /p confirm="Продолжить? (y/N): "
    if /i "%confirm%"=="y" (
        echo Очищаем данные...
        docker compose -f docker-compose.windows.yml down -v
        docker system prune -f
        echo Данные очищены.
    ) else (
        echo Отменено.
    )
    pause
) else if "%choice%"=="6" (
    echo До свидания!
    exit /b 0
) else (
    echo Неверный выбор!
    pause
)

goto :eof
