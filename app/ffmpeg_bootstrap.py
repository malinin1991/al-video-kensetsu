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
_version_label: str | None = None


def get_ffmpeg_version_label() -> str:
    """
    Короткая метка для metadata (encoding_tool / Writing application).
    Пример: «ffmpeg n9.0.1-6-g9d4ca21220-20260822»
    """
    global _version_label
    if _version_label is not None:
        return _version_label

    import re
    import subprocess

    try:
        result = subprocess.run(
            [get_ffmpeg_bin(), '-version'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            first = result.stdout.splitlines()[0].strip()
            # ffmpeg version n9.0.1-... Copyright ...
            match = re.search(r'ffmpeg\s+version\s+(\S+)', first, re.IGNORECASE)
            if match:
                _version_label = f'ffmpeg {match.group(1)}'
            elif 'version' in first.lower():
                _version_label = first.split('Copyright')[0].strip()
            else:
                _version_label = first[:80]
        else:
            _version_label = 'ffmpeg'
    except Exception:
        _version_label = 'ffmpeg'
    return _version_label

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
