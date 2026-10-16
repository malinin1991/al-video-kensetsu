"""Персистентные настройки приложения (ник, параллельность и др.)."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger('video_encoder')

_DEFAULT_SETTINGS_PATH = '/app/settings/app_settings.json'
# Старые пути — только для чтения/миграции
_LEGACY_SETTINGS_PATHS = (
    '/app/logs/app_settings.json',
    '/app/data/app_settings.json',
)
_lock = threading.Lock()
_MAX_NICKNAME_LEN = 64

MIN_CONCURRENT_ENCODES = 1
MAX_CONCURRENT_ENCODES_LIMIT = 16
DEFAULT_CONCURRENT_ENCODES = 1


def settings_path() -> str:
    configured = os.getenv('APP_SETTINGS_PATH', '').strip()
    if configured:
        return configured
    return _DEFAULT_SETTINGS_PATH


def _empty_settings() -> dict[str, Any]:
    return {
        # None = ключ ещё не сохраняли (fallback на env)
        'metadata_nickname': None,
        'max_concurrent_encodes': None,
    }


def _normalize_nickname(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()[:_MAX_NICKNAME_LEN]


def _clamp_concurrent(value: int) -> int:
    if value < MIN_CONCURRENT_ENCODES:
        return MIN_CONCURRENT_ENCODES
    if value > MAX_CONCURRENT_ENCODES_LIMIT:
        return MAX_CONCURRENT_ENCODES_LIMIT
    return value


def normalize_max_concurrent_encodes(value: Any) -> int:
    """
    Нормализует значение лимита параллельности.
    Бросает ValueError при некорректном типе/диапазоне (для API).
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(
            f'Макс. одновременных кодирований должно быть целым числом '
            f'от {MIN_CONCURRENT_ENCODES} до {MAX_CONCURRENT_ENCODES_LIMIT}'
        )
    if isinstance(value, str) and not value.strip():
        raise ValueError(
            f'Укажите целое число от {MIN_CONCURRENT_ENCODES} '
            f'до {MAX_CONCURRENT_ENCODES_LIMIT}'
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'Макс. одновременных кодирований должно быть целым числом '
            f'от {MIN_CONCURRENT_ENCODES} до {MAX_CONCURRENT_ENCODES_LIMIT}'
        ) from exc
    if parsed < MIN_CONCURRENT_ENCODES or parsed > MAX_CONCURRENT_ENCODES_LIMIT:
        raise ValueError(
            f'Макс. одновременных кодирований: от {MIN_CONCURRENT_ENCODES} '
            f'до {MAX_CONCURRENT_ENCODES_LIMIT}'
        )
    return parsed


def _normalize_max_concurrent_saved(value: Any) -> int | None:
    """Для чтения из файла: None если ключа нет/битый; иначе clamp."""
    if value is None:
        return None
    try:
        return _clamp_concurrent(int(value))
    except (TypeError, ValueError):
        return None


def _parse_env_max_concurrent() -> int:
    """Читает MAX_CONCURRENT_ENCODES из env (минимум 1, по умолчанию 1)."""
    raw = os.environ.get(
        'MAX_CONCURRENT_ENCODES',
        str(DEFAULT_CONCURRENT_ENCODES),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            f'Некорректный MAX_CONCURRENT_ENCODES={raw!r}, '
            f'используем {DEFAULT_CONCURRENT_ENCODES}'
        )
        return DEFAULT_CONCURRENT_ENCODES
    if value < MIN_CONCURRENT_ENCODES:
        logger.warning(
            f'MAX_CONCURRENT_ENCODES={value} < {MIN_CONCURRENT_ENCODES}, '
            f'используем {MIN_CONCURRENT_ENCODES}'
        )
        return MIN_CONCURRENT_ENCODES
    return _clamp_concurrent(value)


def _read_file(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return _empty_settings()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return _empty_settings()
        result = _empty_settings()
        if 'metadata_nickname' in data:
            result['metadata_nickname'] = _normalize_nickname(
                data.get('metadata_nickname')
            )
        if 'max_concurrent_encodes' in data:
            result['max_concurrent_encodes'] = _normalize_max_concurrent_saved(
                data.get('max_concurrent_encodes')
            )
        return result
    except Exception as exc:
        logger.warning(f'Не удалось прочитать настройки {path}: {exc}')
        return _empty_settings()


def _has_any_saved(data: dict[str, Any]) -> bool:
    return (
        data.get('metadata_nickname') is not None
        or data.get('max_concurrent_encodes') is not None
    )


def _load_settings_unlocked() -> dict[str, Any]:
    path = settings_path()
    data = _read_file(path)
    if _has_any_saved(data):
        return data

    # Миграция со старых расположений
    if not os.getenv('APP_SETTINGS_PATH', '').strip():
        for legacy in _LEGACY_SETTINGS_PATHS:
            legacy_data = _read_file(legacy)
            if _has_any_saved(legacy_data):
                return legacy_data

    return data


def load_settings() -> dict[str, Any]:
    with _lock:
        return _load_settings_unlocked()


def _atomic_write(path: str, payload: dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    os.replace(tmp_path, path)


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Обновляет и сохраняет настройки в /app/settings (том settings)."""
    path = settings_path()
    with _lock:
        current = _load_settings_unlocked()

        if 'metadata_nickname' in updates:
            current['metadata_nickname'] = _normalize_nickname(
                updates.get('metadata_nickname')
            )
        if 'max_concurrent_encodes' in updates:
            current['max_concurrent_encodes'] = normalize_max_concurrent_encodes(
                updates.get('max_concurrent_encodes')
            )

        _atomic_write(path, current)
        logger.info(f'Настройки сохранены: {path}')
        return dict(current)


def has_saved_nickname() -> bool:
    """True, если ник когда-либо сохраняли в файл (включая пустой)."""
    return load_settings().get('metadata_nickname') is not None


def has_saved_max_concurrent() -> bool:
    """True, если лимит параллельности сохранён в файле."""
    return load_settings().get('max_concurrent_encodes') is not None


def get_metadata_nickname() -> str:
    """
    Ник для формы/кодирования.
    Если ник сохранён в файле (даже пустой) — он приоритетнее env.
    """
    saved = load_settings().get('metadata_nickname')
    if saved is not None:
        return str(saved)
    return os.getenv('METADATA_NICKNAME', '').strip()


def get_max_concurrent_encodes() -> int:
    """
    Лимит одновременных кодирований.
    Файл настроек приоритетнее env; иначе DEFAULT_CONCURRENT_ENCODES (1).
    """
    saved = load_settings().get('max_concurrent_encodes')
    if saved is not None:
        return _clamp_concurrent(int(saved))
    return _parse_env_max_concurrent()
