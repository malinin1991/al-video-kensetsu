# Makefile для системы кодирования видео

.PHONY: help build up down logs clean test dev prod registry-push registry-pull

# Цвета для вывода
GREEN=\033[0;32m
YELLOW=\033[1;33m
RED=\033[0;31m
NC=\033[0m # No Color

help: ## Показать справку
	@echo "$(GREEN)Система пакетного кодирования видео$(NC)"
	@echo "=================================="
	@echo ""
	@echo "$(YELLOW)Доступные команды:$(NC)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Собрать Docker образ
	@echo "$(YELLOW)Сборка Docker образа...$(NC)"
	docker-compose build

build-multiarch: ## Собрать мультиплатформенный Docker образ
	@echo "$(YELLOW)Сборка мультиплатформенного Docker образа...$(NC)"
	@echo "Поддерживаемые архитектуры: linux/amd64, linux/arm64/v8"
	docker buildx build --builder=container \
		--platform linux/amd64,linux/arm64/v8 \
		--provenance=false --sbom=false \
		-f docker/Dockerfile \
		--build-arg BUILD_TIME=$$(date -u +"%Y-%m-%dT%H:%M:%SZ") \
		--build-arg GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown) \
		-t video-encoder:latest .

build-local: ## Собрать образ для текущей архитектуры
	@echo "$(YELLOW)Сборка Docker образа для текущей архитектуры...$(NC)"
	@ARCH=$$(uname -m) && echo "Архитектура: $$ARCH"
	docker-compose build --no-cache

up: ## Запустить систему
	@echo "$(YELLOW)Запуск системы...$(NC)"
	docker-compose up -d
	@echo "$(GREEN)Система запущена!$(NC)"
	@echo "Откройте браузер: http://localhost:5000"

down: ## Остановить систему
	@echo "$(YELLOW)Остановка системы...$(NC)"
	docker-compose down
	@echo "$(GREEN)Система остановлена!$(NC)"

logs: ## Показать логи
	@echo "$(YELLOW)Показ логов...$(NC)"
	docker-compose logs -f

clean: ## Очистить систему
	@echo "$(YELLOW)Очистка системы...$(NC)"
	docker-compose down -v
	docker system prune -f
	@echo "$(GREEN)Система очищена!$(NC)"

test: ## Запустить тесты
	@echo "$(YELLOW)Запуск тестов...$(NC)"
	docker-compose -f docker-compose.test.yml up --build
	@echo "$(GREEN)Тесты завершены!$(NC)"

dev: ## Запустить в режиме разработки
	@echo "$(YELLOW)Запуск в режиме разработки...$(NC)"
	docker-compose -f docker-compose.dev.yml up --build
	@echo "$(GREEN)Режим разработки запущен!$(NC)"

prod: ## Запустить в режиме продакшена
	@echo "$(YELLOW)Запуск в режиме продакшена...$(NC)"
	docker-compose -f docker-compose.prod.yml up --build -d
	@echo "$(GREEN)Продакшен режим запущен!$(NC)"

status: ## Показать статус системы
	@echo "$(YELLOW)Статус системы:$(NC)"
	docker-compose ps

restart: ## Перезапустить систему
	@echo "$(YELLOW)Перезапуск системы...$(NC)"
	docker-compose restart
	@echo "$(GREEN)Система перезапущена!$(NC)"

shell: ## Подключиться к контейнеру
	@echo "$(YELLOW)Подключение к контейнеру...$(NC)"
	docker-compose exec video-encoder /bin/bash

health: ## Проверить здоровье системы
	@echo "$(YELLOW)Проверка здоровья системы...$(NC)"
	curl -f http://localhost:5000/health || echo "$(RED)Система недоступна!$(NC)"

registry-push: ## Собрать и опубликовать образ в registry
	@./scripts/build-and-push.sh

registry-pull: ## Скачать образ из registry и запустить (Linux)
	docker compose --env-file registry.env -f docker-compose.registry.yml pull
	docker compose --env-file registry.env -f docker-compose.registry.yml up -d
