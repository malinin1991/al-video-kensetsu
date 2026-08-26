import os
import hashlib
from werkzeug.utils import secure_filename
import mimetypes


ALLOWED_EXTENSIONS = {'.mp4', '.mkv'}
ALLOWED_MIME_TYPES = {'video/mp4', 'video/x-matroska'}


def allowed_file(filename: str) -> bool:
    """Проверяет, разрешен ли тип файла"""
    if not filename:
        return False
    
    # Проверяем расширение
    _, ext = os.path.splitext(filename.lower())
    if ext in ALLOWED_EXTENSIONS:
        return True
    
    # Проверяем MIME тип
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type in ALLOWED_MIME_TYPES


def calculate_file_hash(file_path: str) -> str:
    """Вычисляет SHA256 хеш файла"""
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def get_file_size(file_path: str) -> int:
    """Возвращает размер файла в байтах"""
    return os.path.getsize(file_path)


def create_secure_filename(original_filename: str) -> str:
    """Создает безопасное имя файла"""
    return secure_filename(original_filename)


def ensure_directory_exists(directory: str):
    """Создает директорию, если она не существует"""
    os.makedirs(directory, exist_ok=True)


def cleanup_file(file_path: str):
    """Безопасно удаляет файл"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError as e:
        print(f"Ошибка при удалении файла {file_path}: {e}")


def get_video_info(file_path: str) -> dict:
    """Получает базовую информацию о видео файле"""
    try:
        file_size = get_file_size(file_path)
        return {
            'size': file_size,
            'size_mb': round(file_size / (1024 * 1024), 2),
            'exists': True
        }
    except Exception as e:
        return {
            'size': 0,
            'size_mb': 0,
            'exists': False,
            'error': str(e)
        }
