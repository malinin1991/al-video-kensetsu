#!/usr/bin/env bash
# Сборка и публикация Docker-образа в приватный registry.
#
#   cp registry.example.env registry.env
#   ./scripts/build-and-push.sh
#
# Опции:
#   --multiarch   мультиарх-сборка (buildx + push)
#   --build-only  только сборка
#   --no-cache    без кэша Docker
#   --env-file    путь к env-файлу (по умолчанию registry.env или .env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

ENV_FILE=""
MULTIARCH=0
BUILD_ONLY=0
NO_CACHE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --multiarch) MULTIARCH=1; shift ;;
        --build-only) BUILD_ONLY=1; shift ;;
        --no-cache) NO_CACHE=1; shift ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

load_env() {
    local file="$1"
    [[ -f "$file" ]] || return 1
    echo "Загрузка переменных из: $file"
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
    return 0
}

if [[ -n "$ENV_FILE" ]]; then
    load_env "$ENV_FILE" || { echo "Файл не найден: $ENV_FILE" >&2; exit 1; }
elif ! load_env "$PROJECT_ROOT/registry.env"; then
    load_env "$PROJECT_ROOT/.env" || true
fi

REGISTRY="${REGISTRY:-registry.mageek.su}"
IMAGE_NAME="${IMAGE_NAME:-al-web-video-encoder}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
DOCKERFILE="docker/Dockerfile"
BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
METADATA_ARGS=(
    --build-arg "BUILD_TIME=${BUILD_TIME}"
    --build-arg "GIT_SHA=${GIT_SHA}"
)

echo "Образ: $FULL_IMAGE"
echo "BUILD_TIME: ${BUILD_TIME}, GIT_SHA: ${GIT_SHA}"

# Номер сборки: инкремент на хосте (в образе только COPY, без +1)
BN_FILE="$PROJECT_ROOT/build_number.txt"
CURRENT=0
if [[ -f "$BN_FILE" ]]; then
    RAW="$(tr -d '[:space:]' < "$BN_FILE" || true)"
    if [[ "$RAW" =~ ^[0-9]+$ ]]; then
        CURRENT="$RAW"
    fi
fi
NEXT=$((CURRENT + 1))
printf '%s' "$NEXT" > "$BN_FILE"
echo "Номер сборки: $NEXT (было $CURRENT)"

if [[ -n "${REGISTRY_USER:-}" && -n "${REGISTRY_PASSWORD:-}" ]]; then
    echo "Вход в registry: $REGISTRY"
    echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USER" --password-stdin
fi

CACHE_FLAG=()
[[ "$NO_CACHE" -eq 1 ]] && CACHE_FLAG=(--no-cache)

if [[ "$MULTIARCH" -eq 1 || "${MULTIARCH:-0}" == "1" ]]; then
    echo "Мультиарх-сборка..."
    BUILDX_ARGS=(
        buildx build
        --builder=container
        --platform "linux/amd64,linux/arm64/v8"
        --provenance=false
        --sbom=false
        -f "$DOCKERFILE"
        -t "$FULL_IMAGE"
        "${METADATA_ARGS[@]}"
        "${CACHE_FLAG[@]}"
        .
    )
    [[ "$BUILD_ONLY" -eq 0 ]] && BUILDX_ARGS+=(--push)
    docker "${BUILDX_ARGS[@]}"
else
    echo "Локальная сборка..."
    docker build "${CACHE_FLAG[@]}" -f "$DOCKERFILE" -t "$FULL_IMAGE" "${METADATA_ARGS[@]}" .
    if [[ "$BUILD_ONLY" -eq 0 ]]; then
        echo "Публикация образа..."
        docker push "$FULL_IMAGE"
    fi
fi

echo ""
echo "Готово: $FULL_IMAGE"
if [[ "$BUILD_ONLY" -eq 1 ]]; then
    echo "Push не выполнялся (--build-only)."
else
    echo "Запуск на сервере:"
    echo "  docker compose --env-file registry.env -f docker-compose.registry.yml pull"
    echo "  docker compose --env-file registry.env -f docker-compose.registry.yml up -d"
fi
