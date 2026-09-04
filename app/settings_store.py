"""Персистентные настройки приложения (ник и др.)."""

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

def settings_path() -> str:
    configured = os.getenv('APP_SETTINGS_PATH', '').strip()
    if configured:
        return configured
    return _DEFAULT_SETTINGS_PATH

def _empty_settings() -> dict[str, Any]:
    return {
        # None = ключ ещё не сохраняли (fallback на env)
        'metadata_nickname': None,
    }

def _normalize_nickname(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()[:_MAX_NICKNAME_LEN]

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
            result['metadata_nickname'] = _normalize_nickname(data.get('metadata_nickname'))
        return result
    except Exception as exc:
        logger.warning(f'Не удалось прочитать настройки {path}: {exc}')
        return _empty_settings()

def _load_settings_unlocked() -> dict[str, Any]:
    path = settings_path()
    data = _read_file(path)
    if data.get('metadata_nickname') is not None:
        return data

    # Миграция со старых расположений
    if not os.getenv('APP_SETTINGS_PATH', '').strip():
        for legacy in _LEGACY_SETTINGS_PATHS:
            legacy_data = _read_file(legacy)
            if legacy_data.get('metadata_nickname') is not None:
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
            current['metadata_nickname'] = _normalize_nickname(updates.get('metadata_nickname'))

        _atomic_write(path, current)
        logger.info(f'Настройки сохранены: {path}')
        return dict(current)

def has_saved_nickname() -> bool:
    """True, если ник когда-либо сохраняли в файл (включая пустой)."""
    return load_settings().get('metadata_nickname') is not None

def get_metadata_nickname() -> str:
    """
    Ник для формы/кодирования.
    Если ник сохранён в файле (даже пустой) — он приоритетнее env.
    """
    saved = load_settings().get('metadata_nickname')
    if saved is not None:
        return str(saved)
    return os.getenv('METADATA_NICKNAME', '').strip()
