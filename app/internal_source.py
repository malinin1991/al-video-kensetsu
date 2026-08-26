"""Проверка пути к файлу на хранилище и копирование в uploads."""
import os
import shutil
import uuid

from app.file_utils import allowed_file, cleanup_file, ensure_directory_exists
from app.logger import logger

DEFAULT_SOURCE_ROOTS = ('/anilibria',)
COPY_BUFFER_SIZE = 4 * 1024 * 1024


class InternalSourceError(Exception):
    """Ошибка внутреннего источника (путь, тип файла, копирование)."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def get_uploads_dir():
    """Каталог загрузок: UPLOADS_DIR или /app/uploads."""
    return os.environ.get('UPLOADS_DIR', '/app/uploads')


def get_internal_source_roots():
    """Разрешённые корни. None — режим выключен (пустой INTERNAL_SOURCE_ROOTS)."""
    raw = os.environ.get('INTERNAL_SOURCE_ROOTS')
    if raw is None:
        parts = list(DEFAULT_SOURCE_ROOTS)
    else:
        parts = [item.strip() for item in raw.split(',') if item.strip()]
        if not parts:
            return None
    return parts


def normalize_source_path(path):
    """Снимает пробелы и парные кавычки по краям пути."""
    if path is None:
        return ''
    value = str(path).strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value


def _path_is_inside(path, root):
    """True, если path находится внутри root (после abspath)."""
    path_abs = os.path.normcase(os.path.abspath(path))
    root_abs = os.path.normcase(os.path.abspath(root))
    if path_abs == root_abs:
        return True
    prefix = root_abs if root_abs.endswith(os.sep) else root_abs + os.sep
    return path_abs.startswith(prefix)


def _safe_realpath(path):
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def is_temp_upload_path(file_path):
    """True, если путь лежит в каталоге uploads (включая копии internal)."""
    if not file_path:
        return False
    return _path_is_inside(_safe_realpath(file_path), _safe_realpath(get_uploads_dir()))


def is_internal_source_path(file_path):
    """True, если путь внутри INTERNAL_SOURCE_ROOTS (read-only библиотека)."""
    if not file_path:
        return False
    roots = get_internal_source_roots()
    if not roots:
        return False
    resolved = _safe_realpath(file_path)
    for root in roots:
        if _path_is_inside(resolved, _safe_realpath(root)):
            return True
    return False


def cleanup_job_original(file_path):
    """Удаляет временный исходник. Internal/read-only не трогает.

    Возвращает True, если файл был удалён.
    """
    if not file_path or is_internal_source_path(file_path):
        return False
    existed = os.path.exists(file_path)
    cleanup_file(file_path)
    return existed and not os.path.exists(file_path)


def resolve_internal_source_path(path):
    """Нормализует путь, проверяет jail и тип файла. Возвращает realpath."""
    roots = get_internal_source_roots()
    if roots is None:
        raise InternalSourceError('Внутренний режим кодирования отключён', 403)

    normalized = normalize_source_path(path)
    if not normalized:
        raise InternalSourceError('Путь к файлу не указан', 400)
    if not os.path.isabs(normalized):
        raise InternalSourceError('Путь должен быть абсолютным', 400)

    resolved = os.path.realpath(normalized)
    allowed = False
    for root in roots:
        root_real = os.path.realpath(root)
        if _path_is_inside(resolved, root_real):
            allowed = True
            break
    if not allowed:
        raise InternalSourceError('Путь вне разрешённых корней', 403)

    if not os.path.exists(resolved):
        raise InternalSourceError('Файл не найден', 404)
    if not os.path.isfile(resolved):
        raise InternalSourceError('Указанный путь не является файлом', 400)
    if not allowed_file(os.path.basename(resolved)):
        raise InternalSourceError('Неподдерживаемый тип файла', 400)

    return resolved


def copy_internal_source_to_uploads(source_path, uploads_dir=None):
    """Копирует файл в uploads. Возвращает (путь_копии, оригинальный_basename).

    Частичный файл удаляется при ошибке. Hardlink не используется.
    """
    dest_dir = uploads_dir or get_uploads_dir()
    ensure_directory_exists(dest_dir)
    original_name = os.path.basename(source_path)
    dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}_{original_name}")

    logger.upload_logger.info(
        f"Копирование внутреннего файла: {source_path} -> {dest_path}"
    )
    try:
        with open(source_path, 'rb') as src, open(dest_path, 'wb') as dst:
            shutil.copyfileobj(src, dst, length=COPY_BUFFER_SIZE)
        shutil.copystat(source_path, dest_path)
    except InternalSourceError:
        raise
    except Exception as exc:
        cleanup_file(dest_path)
        logger.upload_logger.error(f"Не удалось скопировать файл {source_path}: {exc}")
        raise InternalSourceError('Не удалось скопировать файл', 500) from exc

    size = os.path.getsize(dest_path)
    logger.upload_logger.info(f"Файл скопирован: {dest_path} ({size} байт)")
    return dest_path, original_name
