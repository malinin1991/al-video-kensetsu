"""
Обеспечение наличия ffmpeg/ffprobe при старте приложения.

По умолчанию бинарники ожидаются в $FFMPEG_DIR/bin (по умолчанию /app/ffmpeg/bin).
Если их нет — скачивается BtbN FFmpeg n9.0 GPL под архитектуру контейнера.
Пользовательские бинарники (bind-mount) пропускают загрузку.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_FFMPEG_DIR = "/app/ffmpeg"
BTBN_BASE_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
)
ARCH_TARBALLS = {
    "x86_64": "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz",
    "amd64": "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz",
    "aarch64": "ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz",
    "arm64": "ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz",
}

_resolved: tuple[str, str] | None = None
# Кэш FDK/версии привязан к (resolved path, mtime) — сброс при смене бинарника
_ffmpeg_probe_cache_key: tuple[str, float | None] | None = None
_version_label: str | None = None
_version_token: str | None = None
_libfdk_aac_available: bool | None = None
_ffmpeg_build_info: dict | None = None


def _ffmpeg_probe_identity() -> tuple[str, float | None]:
    path = get_ffmpeg_bin()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    return path, mtime


def _invalidate_ffmpeg_probe_cache_if_stale() -> None:
    """Сбрасывает кэш детекции, если путь/mtime ffmpeg изменились."""
    global _ffmpeg_probe_cache_key, _version_label, _version_token
    global _libfdk_aac_available, _ffmpeg_build_info
    identity = _ffmpeg_probe_identity()
    if _ffmpeg_probe_cache_key != identity:
        _ffmpeg_probe_cache_key = identity
        _version_label = None
        _version_token = None
        _libfdk_aac_available = None
        _ffmpeg_build_info = None


def clear_ffmpeg_probe_cache() -> None:
    """Принудительно сбрасывает кэш версии/FDK/build info (для тестов)."""
    global _ffmpeg_probe_cache_key, _version_label, _version_token
    global _libfdk_aac_available, _ffmpeg_build_info
    _ffmpeg_probe_cache_key = None
    _version_label = None
    _version_token = None
    _libfdk_aac_available = None
    _ffmpeg_build_info = None


def has_libfdk_aac() -> bool:
    """True, если в текущем FFmpeg есть энкодер libfdk_aac."""
    global _libfdk_aac_available
    _invalidate_ffmpeg_probe_cache_if_stale()
    if _libfdk_aac_available is not None:
        return _libfdk_aac_available

    import subprocess

    try:
        result = subprocess.run(
            [get_ffmpeg_bin(), '-encoders'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (result.stdout or '') + (result.stderr or '')
        # Строка вида: " A....D libfdk_aac ..."
        _libfdk_aac_available = any(
            line.split() and 'libfdk_aac' in line.split()
            for line in output.splitlines()
        )
    except Exception:
        _libfdk_aac_available = False
    return _libfdk_aac_available


def get_ffmpeg_build_info() -> dict:
    """
    Разбор вывода `ffmpeg -version`: метка версии, configuration, версии libav*.
    Кэш сбрасывается при смене пути/mtime бинарника.
    """
    global _ffmpeg_build_info, _version_label, _version_token
    _invalidate_ffmpeg_probe_cache_if_stale()
    if _ffmpeg_build_info is not None:
        return _ffmpeg_build_info

    import re
    import subprocess

    info = {
        'version_token': None,
        'version_label': None,
        'configuration': None,
        'libraries': {},
    }
    try:
        result = subprocess.run(
            [get_ffmpeg_bin(), '-version'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            _ffmpeg_build_info = info
            return info

        lines = result.stdout.splitlines()
        if lines:
            first = lines[0].strip()
            match = re.search(r'ffmpeg\s+version\s+(\S+)', first, re.IGNORECASE)
            if match:
                token = match.group(1)
                info['version_token'] = token
                info['version_label'] = f'ffmpeg {token}'
            else:
                fallback = first.split('Copyright')[0].strip()[:120]
                info['version_label'] = fallback
                info['version_token'] = (
                    fallback[7:].strip()
                    if fallback.lower().startswith('ffmpeg ')
                    else fallback
                )

        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith('configuration:'):
                info['configuration'] = stripped.split(':', 1)[1].strip()
                continue
            lib_match = re.match(
                r'(libavutil|libavcodec|libavformat|libavdevice|libavfilter|'
                r'libswscale|libswresample|libpostproc)\s+(.+)',
                stripped,
            )
            if lib_match:
                info['libraries'][lib_match.group(1)] = lib_match.group(2).strip()
    except Exception:
        pass

    _ffmpeg_build_info = info
    if info.get('version_label'):
        _version_label = info['version_label']
    if info.get('version_token'):
        _version_token = info['version_token']
    return info


def get_ffmpeg_version_label() -> str:
    """
    Полная метка для UI / metadata (encoding_tool / Writing application).
    Пример: «ffmpeg n9.0.1-6-g9d4ca21220-20260822»
    """
    global _version_label
    _invalidate_ffmpeg_probe_cache_if_stale()
    if _version_label is not None:
        return _version_label

    info = get_ffmpeg_build_info()
    _version_label = info.get('version_label') or 'ffmpeg'
    return _version_label


def get_ffmpeg_version_token() -> str:
    """
    Короткий токен версии без префикса «ffmpeg ».
    Пример: «n9.0.1-6-g9d4ca21220-20260822»
    """
    global _version_token
    _invalidate_ffmpeg_probe_cache_if_stale()
    if _version_token is not None:
        return _version_token

    info = get_ffmpeg_build_info()
    token = info.get('version_token')
    if token:
        _version_token = token
        return _version_token

    label = info.get('version_label') or get_ffmpeg_version_label()
    if label.lower().startswith('ffmpeg '):
        _version_token = label[7:].strip() or 'unknown'
    else:
        _version_token = label or 'unknown'
    return _version_token

def ffmpeg_dir() -> Path:
    return Path(os.environ.get("FFMPEG_DIR", DEFAULT_FFMPEG_DIR))

def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")

def _is_usable_binary(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.access(path, os.X_OK):
        return True
    # Bind-mount / том без +x — пробуем выставить права
    try:
        _chmod_executable(path)
    except OSError:
        return False
    return os.access(path, os.X_OK)

def resolve_bin_paths() -> tuple[Path, Path]:
    """Возвращает ожидаемые пути к ffmpeg и ffprobe."""
    bin_dir = ffmpeg_dir() / "bin"
    ffmpeg_override = os.environ.get("FFMPEG_BIN", "").strip()
    ffprobe_override = os.environ.get("FFPROBE_BIN", "").strip()

    ffmpeg = Path(ffmpeg_override) if ffmpeg_override else bin_dir / "ffmpeg"
    ffprobe = Path(ffprobe_override) if ffprobe_override else bin_dir / "ffprobe"
    return ffmpeg, ffprobe

def get_ffmpeg_bin() -> str:
    """Путь к ffmpeg (после ensure_ffmpeg или из env/PATH)."""
    if _resolved:
        return _resolved[0]
    ffmpeg, _ = resolve_bin_paths()
    if _is_usable_binary(ffmpeg):
        return str(ffmpeg)
    return os.environ.get("FFMPEG_BIN", "ffmpeg")

def get_ffprobe_bin() -> str:
    """Путь к ffprobe (после ensure_ffmpeg или из env/PATH)."""
    if _resolved:
        return _resolved[1]
    _, ffprobe = resolve_bin_paths()
    if _is_usable_binary(ffprobe):
        return str(ffprobe)
    return os.environ.get("FFPROBE_BIN", "ffprobe")

def _arch_tarball_name() -> str:
    machine = platform.machine().lower()
    tarball = ARCH_TARBALLS.get(machine)
    if not tarball:
        supported = ", ".join(sorted(set(ARCH_TARBALLS)))
        raise RuntimeError(
            f"Неподдерживаемая архитектура для автозагрузки FFmpeg: {machine}. "
            f"Поддерживаются: {supported}. "
            f"Смонтируйте свои бинарники в {ffmpeg_dir() / 'bin'} "
            f"или задайте FFMPEG_BIN / FFPROBE_BIN."
        )
    return tarball

def _download_url() -> str:
    override = os.environ.get("FFMPEG_DOWNLOAD_URL", "").strip()
    if override:
        return override
    return f"{BTBN_BASE_URL}/{_arch_tarball_name()}"

def _chmod_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

def _extract_bins_from_tar(archive_path: Path, dest_bin: Path) -> None:
    dest_bin.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:xz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        ffmpeg_member = None
        ffprobe_member = None
        for name, member in members.items():
            normalized = name.replace("\\", "/")
            if normalized.endswith("/bin/ffmpeg") or normalized == "bin/ffmpeg":
                ffmpeg_member = member
            elif normalized.endswith("/bin/ffprobe") or normalized == "bin/ffprobe":
                ffprobe_member = member

        if ffmpeg_member is None or ffprobe_member is None:
            raise RuntimeError(
                "В архиве FFmpeg не найдены bin/ffmpeg и bin/ffprobe"
            )

        for member, out_name in (
            (ffmpeg_member, "ffmpeg"),
            (ffprobe_member, "ffprobe"),
        ):
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"Не удалось извлечь {member.name} из архива")
            out_path = dest_bin / out_name
            with open(out_path, "wb") as out_f:
                shutil.copyfileobj(extracted, out_f)
            _chmod_executable(out_path)

def _download_file(url: str, dest: Path, attempts: int = 5) -> None:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            print(
                f"[ffmpeg-bootstrap] Скачивание FFmpeg "
                f"(попытка {attempt}/{attempts}): {url}",
                flush=True,
            )
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "al-web-video-encoder-ffmpeg-bootstrap"},
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                with open(dest, "wb") as out_f:
                    shutil.copyfileobj(response, out_f)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            print(
                f"[ffmpeg-bootstrap] Ошибка загрузки (попытка {attempt}/{attempts}): {exc}",
                file=sys.stderr,
                flush=True,
            )
    raise RuntimeError(
        f"Не удалось скачать FFmpeg после {attempts} попыток: {url}. "
        f"Последняя ошибка: {last_error}"
    )

def _download_and_install(ffmpeg: Path, ffprobe: Path) -> None:
    """Скачивает и устанавливает бинарники в каталог назначения."""
    # При явных override-путях не качаем «мимо» — пользователь указал конкретные пути
    if os.environ.get("FFMPEG_BIN", "").strip() or os.environ.get(
        "FFPROBE_BIN", ""
    ).strip():
        raise RuntimeError(
            f"FFmpeg/ffprobe не найдены по указанным путям "
            f"(FFMPEG_BIN={ffmpeg}, FFPROBE_BIN={ffprobe}). "
            f"Смонтируйте бинарники или уберите FFMPEG_BIN/FFPROBE_BIN "
            f"для автозагрузки в {ffmpeg_dir() / 'bin'}."
        )

    dest_bin = ffmpeg_dir() / "bin"
    url = _download_url()
    print(
        f"[ffmpeg-bootstrap] Бинарники отсутствуют. "
        f"Загрузка BtbN FFmpeg n9.0 GPL → {dest_bin}",
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="ffmpeg-dl-") as tmp:
        archive_path = Path(tmp) / "ffmpeg.tar.xz"
        _download_file(url, archive_path)
        print("[ffmpeg-bootstrap] Распаковка bin/ffmpeg и bin/ffprobe…", flush=True)
        _extract_bins_from_tar(archive_path, dest_bin)

    if not _is_usable_binary(ffmpeg) or not _is_usable_binary(ffprobe):
        raise RuntimeError(
            f"После установки бинарники недоступны: {ffmpeg}, {ffprobe}"
        )

    print(
        f"[ffmpeg-bootstrap] FFmpeg установлен: {ffmpeg}, {ffprobe}",
        flush=True,
    )

def _resolve_from_path() -> tuple[str, str] | None:
    """Ищет ffmpeg/ffprobe в PATH (локальный запуск вне контейнера)."""
    ffmpeg_name = os.environ.get("FFMPEG_BIN", "").strip() or "ffmpeg"
    ffprobe_name = os.environ.get("FFPROBE_BIN", "").strip() or "ffprobe"
    # Если заданы абсолютные пути — which не подходит
    if os.path.isabs(ffmpeg_name) or os.path.isabs(ffprobe_name):
        return None
    found_ffmpeg = shutil.which(ffmpeg_name)
    found_ffprobe = shutil.which(ffprobe_name)
    if found_ffmpeg and found_ffprobe:
        return found_ffmpeg, found_ffprobe
    return None

def ensure_ffmpeg() -> tuple[str, str]:
    """
    Гарантирует наличие исполняемых ffmpeg и ffprobe.

    Returns:
        (путь_к_ffmpeg, путь_к_ffprobe)

    Raises:
        SystemExit при неустранимой ошибке (для вызова из entrypoint/run.py).
    """
    global _resolved

    ffmpeg, ffprobe = resolve_bin_paths()

    if _is_usable_binary(ffmpeg) and _is_usable_binary(ffprobe):
        print(
            f"[ffmpeg-bootstrap] Используются существующие бинарники: "
            f"{ffmpeg}, {ffprobe}",
            flush=True,
        )
        _resolved = (str(ffmpeg), str(ffprobe))
        return _resolved

    # Вне Linux (или без каталога контейнера) — системный FFmpeg из PATH
    if sys.platform != "linux":
        from_path = _resolve_from_path()
        if from_path:
            print(
                f"[ffmpeg-bootstrap] Используются системные бинарники из PATH: "
                f"{from_path[0]}, {from_path[1]}",
                flush=True,
            )
            _resolved = from_path
            return _resolved

    if _truthy(os.environ.get("FFMPEG_SKIP_DOWNLOAD")):
        msg = (
            f"FFmpeg не найден ({ffmpeg}, {ffprobe}), "
            f"а FFMPEG_SKIP_DOWNLOAD включён. "
            f"Смонтируйте бинарники в {ffmpeg_dir() / 'bin'} "
            f"или задайте FFMPEG_BIN / FFPROBE_BIN."
        )
        print(f"[ffmpeg-bootstrap] ОШИБКА: {msg}", file=sys.stderr, flush=True)
        raise SystemExit(1)

    if sys.platform != "linux":
        msg = (
            "Автозагрузка BtbN FFmpeg поддерживается только в Linux-контейнере. "
            "Установите FFmpeg в систему или задайте FFMPEG_BIN / FFPROBE_BIN."
        )
        print(f"[ffmpeg-bootstrap] ОШИБКА: {msg}", file=sys.stderr, flush=True)
        raise SystemExit(1)

    try:
        _download_and_install(ffmpeg, ffprobe)
    except Exception as exc:
        print(f"[ffmpeg-bootstrap] ОШИБКА: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc

    _resolved = (str(ffmpeg), str(ffprobe))
    return _resolved

if __name__ == "__main__":
    ensure_ffmpeg()
