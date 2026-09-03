"""Сборка контейнера после encode: JSON-опции mkvmerge (языки, имена, ник, цвет)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Optional

from app.file_utils import cleanup_file

logger = logging.getLogger('video_encoder')

ANILIBERTY_AUDIO_TITLE = 'AniLiberty'
SIGN_SUBTITLE_TITLE = 'Надписи'
SUBTITLE_TITLE = 'Субтитры'

_ORIGINAL_PREFIX_RE = re.compile(
    r'^(Original|Оригинал)(?:$|[\s\[\(\-_])',
    re.IGNORECASE,
)
_NICKNAME_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_MAX_NICKNAME_LEN = 64
_DIMENSIONS_RE = re.compile(r'^[0-9]+x[0-9]+$')
_REGULAR_TRACK_TYPES = frozenset({'video', 'audio', 'subtitles'})

# ISO 639-2 (identify) → ISO 639-1 (как в GUI mkvmerge)
_ISO639_2_TO_1 = {
    'rus': 'ru',
    'jpn': 'ja',
    'eng': 'en',
    'chi': 'zh',
    'zho': 'zh',
    'kor': 'ko',
    'ger': 'de',
    'deu': 'de',
    'fre': 'fr',
    'fra': 'fr',
    'spa': 'es',
    'ita': 'it',
    'por': 'pt',
    'ukr': 'uk',
    'bel': 'be',
    'pol': 'pl',
    'cze': 'cs',
    'ces': 'cs',
}

_UNKNOWN_LANGS = frozenset({'', 'und', 'unk', 'mis', 'zxx'})

# Свойство identify → флаг mkvmerge (только если поле уже есть)
_COLOR_FLAG_MAP = (
    ('color_matrix_coefficients', '--color-matrix-coefficients'),
    ('colour_matrix_coefficients', '--color-matrix-coefficients'),
    ('color_range', '--color-range'),
    ('colour_range', '--color-range'),
    ('color_transfer_characteristics', '--color-transfer-characteristics'),
    ('colour_transfer_characteristics', '--color-transfer-characteristics'),
    ('color_primaries', '--color-primaries'),
    ('colour_primaries', '--color-primaries'),
    ('color_bits_per_channel', '--color-bits-per-channel'),
    ('chroma_subsample', '--chroma-subsample'),
    ('cb_subsample', '--cb-subsample'),
    ('chroma_siting', '--chroma-siting'),
    ('max_content_light', '--max-content-light'),
    ('max_frame_light', '--max-frame-light'),
    ('chromaticity_coordinates', '--chromaticity-coordinates'),
    ('white_color_coordinates', '--white-color-coordinates'),
    ('max_luminance', '--max-luminance'),
    ('min_luminance', '--min-luminance'),
)


def sanitize_nickname(nickname: str) -> str:
    """Нормализует ник: trim, длина, без управляющих символов."""
    cleaned = str(nickname or '').strip()
    if not cleaned:
        return ''
    if len(cleaned) > _MAX_NICKNAME_LEN:
        raise ValueError(f'Ник слишком длинный (макс. {_MAX_NICKNAME_LEN} символов)')
    if _NICKNAME_CONTROL_RE.search(cleaned):
        raise ValueError('Ник не должен содержать управляющие символы (переносы строк и т.п.)')
    return cleaned


def is_original_track_title(title: str) -> bool:
    if not title:
        return False
    return bool(_ORIGINAL_PREFIX_RE.match(title.strip()))


def is_aniliberty_title(title: str) -> bool:
    return (title or '').strip() == ANILIBERTY_AUDIO_TITLE


def is_update_metadata_enabled(params: Optional[dict[str, Any]]) -> bool:
    value = (params or {}).get('update_metadata', True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() in ('true', '1', 'yes', 'on')


def audio_was_encoded(params: Optional[dict[str, Any]]) -> bool:
    codec = str((params or {}).get('audio_codec') or '').strip().lower()
    return codec not in ('', 'none', 'null', 'copy', 'remove')


def get_mkvmerge_bin() -> str:
    configured = os.environ.get('MKVMERGE_BIN', '').strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        raise RuntimeError(
            'mkvmerge не найден: установите mkvtoolnix или задайте MKVMERGE_BIN'
        )
    found = shutil.which('mkvmerge')
    if not found:
        raise RuntimeError(
            'mkvmerge не найден: установите mkvtoolnix или задайте MKVMERGE_BIN'
        )
    return found


def identify_encoded_file(filepath: str, timeout: int = 60) -> dict[str, Any]:
    """Читает дорожки закодированного файла через mkvmerge -J."""
    result = subprocess.run(
        [get_mkvmerge_bin(), '-J', filepath],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f'mkvmerge -J не смог прочитать файл: {detail}')

    try:
        data = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'mkvmerge -J вернул невалидный JSON: {exc}') from exc

    if not isinstance(data, dict):
        raise RuntimeError('mkvmerge -J вернул неожиданный JSON')

    errors = [str(item) for item in (data.get('errors') or []) if item]
    if errors:
        raise RuntimeError(f'mkvmerge -J: {"; ".join(errors)}')

    container = data.get('container') or {}
    if not container.get('recognized') or not container.get('supported'):
        raise RuntimeError('mkvmerge не распознал закодированный файл')

    return data


def write_option_file(path: str, options: list[str]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(options, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def option_file_path(output_path: str, job_id: str = '') -> str:
    """JSON рядом с выходом, либо с id задания в имени."""
    stem, _ = os.path.splitext(output_path)
    if job_id:
        return f'{stem}.{job_id}.mkvmerge.json'
    return f'{stem}.mkvmerge.json'


def remux_temp_path(output_path: str) -> str:
    return f'{output_path}.remux.tmp'


def encode_partial_path(output_path: str) -> str:
    stem, ext = os.path.splitext(output_path)
    return f'{stem}.partial{ext or ".mkv"}'


def job_sidecar_paths(output_path: str, job_id: str = '') -> list[str]:
    """Промежуточные файлы encode/remux: partial, remux.tmp, JSON опций."""
    if not output_path:
        return []
    paths = [
        encode_partial_path(output_path),
        remux_temp_path(output_path),
        option_file_path(output_path, job_id),
    ]
    if job_id:
        without_id = option_file_path(output_path)
        if without_id not in paths:
            paths.append(without_id)
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path and path != output_path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def cleanup_job_sidecars(output_path: str, job_id: str = '') -> None:
    """Удаляет sidecar encode/remux, не трогая финальный выход."""
    for path in job_sidecar_paths(output_path, job_id):
        cleanup_file(path)


def build_mkvmerge_options(
    encoded_path: str,
    output_path: str,
    identify: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
) -> list[str]:
    """
    Собирает JSON-массив аргументов mkvmerge по identify закодированного файла.

    Цвет и размер — только если уже есть в identify. Языки — ISO 639-1.
    Default/forced/original ставятся явно по правилам сборки, не копируются из исходника.
    """
    params = params or {}
    nickname = sanitize_nickname(str(params.get('metadata_nickname') or ''))
    audio_encoded = audio_was_encoded(params)

    encoded_abs = os.path.abspath(encoded_path)
    output_abs = os.path.abspath(output_path)

    # Без --ui-language: Debian mkvtoolnix без перевода ru падает с кодом 2.
    options: list[str] = [
        '--priority', 'lower',
        '--output', output_abs,
        '--no-track-tags',
        '--no-global-tags',
    ]

    tracks = _regular_tracks(identify)
    single_video = sum(1 for track in tracks if track.get('type') == 'video') == 1
    subtitle_titles = _resolve_subtitle_titles(tracks)
    for track in tracks:
        options.extend(
            _options_for_track(
                track,
                nickname=nickname,
                audio_encoded=audio_encoded,
                single_video=single_video,
                subtitle_title=subtitle_titles.get(_track_id(track)),
            )
        )

    options.extend(['(', encoded_abs, ')'])

    if tracks:
        track_order = ','.join(f'0:{_track_id(track)}' for track in tracks)
        options.extend(['--track-order', track_order])

    return options


def _regular_tracks(identify: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for track in identify.get('tracks') or []:
        if not isinstance(track, dict):
            continue
        if track.get('type') in _REGULAR_TRACK_TYPES:
            tracks.append(track)
    return tracks


def _track_id(track: dict[str, Any]) -> int:
    return int(track.get('id', 0))


def _props(track: dict[str, Any]) -> dict[str, Any]:
    props = track.get('properties')
    return props if isinstance(props, dict) else {}


def _options_for_track(
    track: dict[str, Any],
    *,
    nickname: str,
    audio_encoded: bool,
    single_video: bool,
    subtitle_title: Optional[str] = None,
) -> list[str]:
    tid = _track_id(track)
    track_type = str(track.get('type') or '')
    props = _props(track)
    title = str(props.get('track_name') or '')
    language = _language_from_props(props)
    if track_type == 'subtitles' and subtitle_title is not None:
        new_title = subtitle_title
        rule_title = subtitle_title
    else:
        new_title = _apply_nickname(track_type, title, nickname, audio_encoded)
        rule_title = title

    options: list[str] = []

    if track_type == 'subtitles' and _is_text_subtitle(track):
        options.extend(['--sub-charset', f'{tid}:UTF-8'])

    resolved = resolve_language(track_type, rule_title, language)
    if resolved:
        options.extend(['--language', f'{tid}:{resolved}'])

    if new_title:
        options.extend(['--track-name', f'{tid}:{new_title}'])

    options.extend(
        _flag_options_for_track(
            tid,
            track_type,
            rule_title,
            single_video=single_video,
        )
    )

    if track_type == 'video':
        dimensions = _display_dimensions(props)
        if dimensions:
            options.extend(['--display-dimensions', f'{tid}:{dimensions}'])
        options.extend(_color_options(tid, props))

    return options


def _flag_options_for_track(
    tid: int,
    track_type: str,
    title: str,
    *,
    single_video: bool,
) -> list[str]:
    """Явные флаги mkvmerge: исходник может врать, копировать его нельзя."""
    title = (title or '').strip()

    if track_type == 'video':
        flags: list[str] = []
        if single_video:
            flags.extend(_default_forced_flags(tid, enabled=True))
        if is_original_track_title(title):
            flags.extend(['--original-flag', f'{tid}:1'])
        return flags

    if track_type == 'audio' and is_aniliberty_title(title):
        return _default_forced_flags(tid, enabled=True)

    if track_type == 'audio' and is_original_track_title(title):
        return ['--original-flag', f'{tid}:1']

    if track_type == 'subtitles' and title == SIGN_SUBTITLE_TITLE:
        return _default_forced_flags(tid, enabled=True)

    if track_type == 'subtitles' and title == SUBTITLE_TITLE:
        return _default_forced_flags(tid, enabled=False)

    return []


def _resolve_subtitle_titles(tracks: list[dict[str, Any]]) -> dict[int, str]:
    """Имена субтитров по default: «Надписи» / «Субтитры»."""
    subs = [track for track in tracks if track.get('type') == 'subtitles']
    if not subs:
        logger.info(
            'Субтитры в файле отсутствуют: правила именования '
            '«Надписи»/«Субтитры» не применяются'
        )
        return {}

    if len(subs) == 2:
        defaults = [track for track in subs if _is_default_track(track)]
        if len(defaults) == 1:
            default_id = _track_id(defaults[0])
            return {
                _track_id(track): (
                    SIGN_SUBTITLE_TITLE
                    if _track_id(track) == default_id
                    else SUBTITLE_TITLE
                )
                for track in subs
            }
        return {
            _track_id(subs[0]): SIGN_SUBTITLE_TITLE,
            _track_id(subs[1]): SUBTITLE_TITLE,
        }

    return {
        _track_id(track): (
            SIGN_SUBTITLE_TITLE if _is_default_track(track) else SUBTITLE_TITLE
        )
        for track in subs
    }


def _is_default_track(track: dict[str, Any]) -> bool:
    value = _props(track).get('default_track')
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _default_forced_flags(tid: int, *, enabled: bool) -> list[str]:
    bit = '1' if enabled else '0'
    return [
        '--default-track-flag', f'{tid}:{bit}',
        '--forced-display-flag', f'{tid}:{bit}',
    ]


def _apply_nickname(
    track_type: str,
    title: str,
    nickname: str,
    audio_encoded: bool,
) -> str:
    if not nickname:
        return title
    if track_type == 'video':
        updated = _append_nickname_suffix(title or '', nickname)
        return updated if updated is not None else title
    if track_type == 'audio' and audio_encoded and is_original_track_title(title):
        updated = _append_nickname_suffix(title, nickname)
        return updated if updated is not None else title
    return title


def _append_nickname_suffix(title: str, nickname: str) -> Optional[str]:
    suffix = f' [{nickname}]'
    if title.endswith(suffix):
        return None
    if not title.strip():
        return f'[{nickname}]'
    return f'{title}{suffix}'


def resolve_language(track_type: str, title: str, current_language: str) -> Optional[str]:
    """ISO 639-1 для --language, или None если флаг не ставить."""
    current = (current_language or '').strip()
    title = (title or '').strip()

    if track_type == 'audio' and is_aniliberty_title(title):
        return 'ru'

    if title in (SIGN_SUBTITLE_TITLE, SUBTITLE_TITLE):
        return 'ru'

    if is_original_track_title(title):
        compressed = compress_language(current)
        if compressed is None:
            return 'ja'
        return compressed

    return compress_language(current)


def compress_language(code: str) -> Optional[str]:
    cleaned = (code or '').strip().lower().replace('_', '-')
    if not cleaned:
        return None
    primary = cleaned.split('-', 1)[0]
    if primary in _UNKNOWN_LANGS:
        return None
    mapped = _ISO639_2_TO_1.get(primary)
    if mapped:
        return mapped
    if primary.isalpha() and len(primary) in (2, 3):
        return primary
    return None


def _language_from_props(props: dict[str, Any]) -> str:
    language = str(props.get('language') or '').strip()
    if language:
        return language
    ietf = str(props.get('language_ietf') or '').strip()
    if ietf:
        return ietf.split('-', 1)[0]
    return ''


def _is_text_subtitle(track: dict[str, Any]) -> bool:
    props = _props(track)
    if props.get('text_subtitles') is True:
        return True
    codec_id = str(props.get('codec_id') or '')
    return codec_id.upper().startswith('S_TEXT/')


def _display_dimensions(props: dict[str, Any]) -> str:
    for key in ('display_dimensions', 'pixel_dimensions'):
        value = str(props.get(key) or '').strip()
        if value and _DIMENSIONS_RE.match(value):
            return value
    return ''


def _color_options(tid: int, props: dict[str, Any]) -> list[str]:
    options: list[str] = []
    seen_flags: set[str] = set()
    for prop_key, flag in _COLOR_FLAG_MAP:
        if prop_key not in props or flag in seen_flags:
            continue
        value = props[prop_key]
        if value is None or value == '':
            continue
        options.extend([flag, f'{tid}:{value}'])
        seen_flags.add(flag)
    return options
