"""Selective stereo downmix (ATSC A/52 LoRo, без LFE)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from app.execution import run_media
from typing import Callable


# ATSC A/52 LoRo без LFE; нормализация через `<` в pan
ATSC_LORO_PAN = (
    "pan=stereo|"
    "FL < FL + 0.707*FC + 0.707*BL + 0.707*SL|"
    "FR < FR + 0.707*FC + 0.707*BR + 0.707*SR"
)

AUDIO_MAP_ALL_RE = re.compile(r"^0:a\?$")
AUDIO_MAP_INDEX_RE = re.compile(r"^0:a:(\d+)$")


@dataclass(frozen=True)
class AudioStreamInfo:
    """Описание аудиопотока из ffprobe (порядок = индекс среди a:*)."""
    aindex: int
    channels: int
    channel_layout: str | None = None
    codec_index: int | None = None


@dataclass(frozen=True)
class AudioMapSpec:
    """Разобранный audio_map: все дорожки или одна по индексу a:N."""
    mode: str  # 'all' | 'index'
    index: int | None = None


class AudioDownmixError(ValueError):
    """Ошибка probe / неподдерживаемый audio_map / downmix."""


def needs_downmix(channels: int | None) -> bool:
    """Downmix только для многоканального (>2) аудио."""
    return channels is not None and channels > 2


def channel_skip_reason(channels: int | None) -> str:
    if channels is None:
        return "unknown"
    if channels <= 1:
        return f"mono, {channels}ch"
    if channels == 2:
        return f"stereo, {channels}ch"
    return f"{channels}ch"


def parse_audio_map(audio_map: str | None) -> AudioMapSpec:
    """Поддерживает только 0:a? и 0:a:N."""
    raw = (audio_map or "").strip()
    if not raw:
        raise AudioDownmixError("Пустой audio_map при stereo downmix")
    if AUDIO_MAP_ALL_RE.match(raw):
        return AudioMapSpec(mode="all")
    match = AUDIO_MAP_INDEX_RE.match(raw)
    if match:
        return AudioMapSpec(mode="index", index=int(match.group(1)))
    raise AudioDownmixError(
        f"Неподдерживаемый audio_map для stereo downmix: {audio_map!r} "
        f"(допустимы 0:a? и 0:a:N)"
    )


def probe_audio_streams(
    input_path: str,
    ffprobe_bin: str,
    *,
    timeout: float = 30,
    run: Callable = run_media,
) -> list[AudioStreamInfo]:
    """ffprobe всех аудиопотоков: channels / channel_layout / index.

    Raises:
        AudioDownmixError: probe не удался.
    """
    try:
        result = run(
            [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,channels,channel_layout",
                "-of", "json",
                input_path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioDownmixError(
            f"Не удалось выполнить ffprobe для аудио: {exc}"
        ) from exc

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise AudioDownmixError(
            f"ffprobe аудио завершился с кодом {result.returncode}"
            + (f": {err}" if err else "")
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioDownmixError(
            f"ffprobe вернул невалидный JSON: {exc}"
        ) from exc

    streams = payload.get("streams") or []
    parsed: list[AudioStreamInfo] = []
    for aindex, stream in enumerate(streams):
        channels_raw = stream.get("channels")
        try:
            channels = int(channels_raw) if channels_raw is not None else None
        except (TypeError, ValueError):
            channels = None
        if channels is None:
            raise AudioDownmixError(
                f"ffprobe не вернул channels для аудиопотока a:{aindex}"
            )
        layout = stream.get("channel_layout")
        codec_index = stream.get("index")
        try:
            codec_index = int(codec_index) if codec_index is not None else None
        except (TypeError, ValueError):
            codec_index = None
        parsed.append(
            AudioStreamInfo(
                aindex=aindex,
                channels=channels,
                channel_layout=str(layout) if layout else None,
                codec_index=codec_index,
            )
        )
    return parsed


def select_streams_for_map(
    streams: list[AudioStreamInfo],
    audio_map: str | None,
) -> list[AudioStreamInfo]:
    """Выбирает потоки по audio_map (0:a? / 0:a:N)."""
    spec = parse_audio_map(audio_map)
    if spec.mode == "all":
        return list(streams)
    assert spec.index is not None
    if spec.index < 0 or spec.index >= len(streams):
        raise AudioDownmixError(
            f"audio_map 0:a:{spec.index} вне диапазона "
            f"(аудиопотоков: {len(streams)})"
        )
    return [streams[spec.index]]


@dataclass
class DownmixPlan:
    """План per-stream map / filter_complex для stereo downmix."""
    filter_complex: str | None
    audio_maps: list[str]  # аргументы для -map (по одному на поток)
    log_lines: list[str]


def build_downmix_plan(
    streams: list[AudioStreamInfo],
    audio_map: str | None,
    *,
    pan_filter: str = ATSC_LORO_PAN,
) -> DownmixPlan:
    """Строит filter_complex и -map только для channels > 2."""
    selected = select_streams_for_map(streams, audio_map)
    if not selected:
        raise AudioDownmixError("Нет аудиопотоков для stereo downmix")

    filter_parts: list[str] = []
    maps: list[str] = []
    logs: list[str] = []
    filter_idx = 0

    for stream in selected:
        label = f"0:a:{stream.aindex}"
        layout = stream.channel_layout or "?"
        if needs_downmix(stream.channels):
            out_label = f"dm{filter_idx}"
            filter_parts.append(f"[{label}]{pan_filter}[{out_label}]")
            maps.append(f"[{out_label}]")
            logs.append(
                f"audio a:{stream.aindex}: downmix "
                f"({stream.channels}ch, {layout}) → stereo"
            )
            filter_idx += 1
        else:
            maps.append(label)
            reason = channel_skip_reason(stream.channels)
            logs.append(f"audio a:{stream.aindex}: skip ({reason})")

    filter_complex = ";".join(filter_parts) if filter_parts else None
    return DownmixPlan(
        filter_complex=filter_complex,
        audio_maps=maps,
        log_lines=logs,
    )
