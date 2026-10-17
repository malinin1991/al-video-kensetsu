"""Атомарное хранение user state; поставляемые YAML никогда не записываются."""
from __future__ import annotations

import os
import logging
from pathlib import Path
import tempfile

from app.preset_schema import CatalogError, canonical, empty_state, fingerprint, normalize_state, parse_json

logger = logging.getLogger(__name__)


def configured_path():
    value = os.environ.get('USER_PRESETS_PATH', '/app/settings/presets/catalog.json')
    if not os.path.isabs(value):
        raise CatalogError('USER_PRESETS_PATH должен быть абсолютным', 'storage_unavailable', 503)
    return Path(value)


def fsync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path, payload):
    """Исключение после replace явно отличается от отказа до commit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    replaced = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        replaced = True
        fsync_directory(path.parent)
    except Exception as exc:
        raise CatalogError('Результат сохранения неизвестен. Прочитайте каталог перед повтором.' if replaced else f'Не удалось сохранить каталог: {exc}', 'save_result_unknown' if replaced else 'storage_unavailable', 503) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class PresetStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else configured_path()

    def load(self):
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return empty_state()
        except OSError as exc:
            raise CatalogError(f'Не удалось прочитать пользовательский каталог: {exc}', 'storage_unavailable', 503) from exc
        try:
            return normalize_state(parse_json(data))
        except CatalogError as exc:
            raise CatalogError(f'Пользовательский каталог повреждён или несовместим: {exc}. Остановите сервер и выполните python -m app.preset_migration restore --backup <файл> --target-digest <sha256>.', exc.code if exc.code == 'unsupported_schema' else 'storage_corrupt', 503) from exc

    def save(self, previous, candidate):
        previous, candidate = normalize_state(previous), normalize_state(candidate)
        backups = self.path.parent / 'backups'
        try:
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups / f'{fingerprint(previous)}.json'
            atomic_write(backup, canonical(previous) + b'\n')
            atomic_write(self.path, canonical(candidate) + b'\n')
        except CatalogError:
            raise
        except OSError as exc:
            raise CatalogError(f'Не удалось сохранить каталог: {exc}', 'storage_unavailable', 503) from exc

        # Основной файл уже сохранён. Ошибка обслуживания backups не отменяет
        # commit и не должна оставлять менеджер со старым снимком в памяти.
        try:
            expired = sorted(backups.glob('*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)[20:]
            for old in expired:
                try:
                    old.unlink()
                except OSError:
                    logger.warning('Не удалось удалить старую копию пресетов %s', old, exc_info=True)
        except OSError:
            logger.warning('Не удалось проверить срок хранения копий пресетов', exc_info=True)
