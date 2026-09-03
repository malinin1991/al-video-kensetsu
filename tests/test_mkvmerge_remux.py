"""Тесты JSON-опций mkvmerge: языки, ник, charset, цвет."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.mkvmerge_remux import (
    build_mkvmerge_options,
    cleanup_job_sidecars,
    compress_language,
    get_mkvmerge_bin,
    identify_encoded_file,
    is_aniliberty_title,
    is_original_track_title,
    job_sidecar_paths,
    resolve_language,
    sanitize_nickname,
)


def _track(tid: int, track_type: str, **props):
    return {
        'id': tid,
        'type': track_type,
        'codec': props.pop('codec', 'unknown'),
        'properties': props,
    }


def _sample_identify(*, color=True, pgs=True) -> dict:
    video_props = {
        'codec_id': 'V_MPEGH/ISO/HEVC',
        'pixel_dimensions': '1920x1080',
        'display_dimensions': '1920x1080',
        'language': 'und',
        'track_name': 'Original',
    }
    if color:
        video_props.update({
            'color_matrix_coefficients': 1,
            'color_range': 1,
            'color_transfer_characteristics': 1,
            'color_primaries': 1,
        })

    tracks = [
        _track(0, 'video', codec='HEVC/H.265/MPEG-H', **video_props),
        _track(
            1, 'audio',
            codec='Opus',
            codec_id='A_OPUS',
            language='eng',
            track_name='AniLiberty',
        ),
        _track(
            2, 'audio',
            codec='AAC',
            codec_id='A_AAC',
            language='jpn',
            track_name='Original',
        ),
        _track(
            3, 'subtitles',
            codec='SubRip/SRT',
            codec_id='S_TEXT/UTF8',
            text_subtitles=True,
            language='',
            track_name='Signs',
            default_track=True,
        ),
        _track(
            4, 'subtitles',
            codec='SubRip/SRT',
            codec_id='S_TEXT/UTF8',
            text_subtitles=True,
            language='rus',
            track_name='Full',
            default_track=False,
        ),
    ]
    if pgs:
        tracks.append(_track(
            5, 'subtitles',
            codec='HDMV PGS',
            codec_id='S_HDMV/PGS',
            language='und',
            track_name='Signs',
            default_track=False,
        ))
    return {
        'identification_format_version': 20,
        'container': {'recognized': True, 'supported': True, 'type': 'Matroska'},
        'tracks': tracks,
    }


def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, item in enumerate(args) if item == flag]


def _build(identify=None, nickname='Nick', audio_codec='copy', encoded='/enc.mkv', output='/out.mkv'):
    return build_mkvmerge_options(
        encoded,
        output,
        identify if identify is not None else _sample_identify(),
        {
            'metadata_nickname': nickname,
            'audio_codec': audio_codec,
            'video_codec': 'libx265',
        },
    )


class TestSanitizeNickname:
    def test_ok(self):
        assert sanitize_nickname('GeeKaZ0iD') == 'GeeKaZ0iD'
        assert sanitize_nickname(' nick_1 ') == 'nick_1'
        assert sanitize_nickname('A@B#C$1!') == 'A@B#C$1!'
        assert sanitize_nickname('Ник [AL]') == 'Ник [AL]'
        assert sanitize_nickname('') == ''

    def test_rejects_control_and_too_long(self):
        with pytest.raises(ValueError, match='управляющие'):
            sanitize_nickname('bad\nnick')
        with pytest.raises(ValueError, match='длинный'):
            sanitize_nickname('x' * 65)


class TestOriginalTitleAndLanguage:
    def test_original_prefixes(self):
        assert is_original_track_title('Original')
        assert is_original_track_title('Original Russian')
        assert is_original_track_title('Оригинал')
        assert is_original_track_title('оригинал [JP]')
        assert not is_original_track_title('originality')
        assert not is_original_track_title('AniLiberty')
        assert not is_original_track_title('')

    def test_aniliberty_ru(self):
        assert is_aniliberty_title('AniLiberty')
        assert is_aniliberty_title('  AniLiberty  ')
        assert not is_aniliberty_title('AniLiberty HD')
        assert resolve_language('audio', 'AniLiberty', 'eng') == 'ru'
        assert resolve_language('audio', '  AniLiberty  ', 'eng') == 'ru'
        assert resolve_language('audio', 'AniLiberty', 'rus') == 'ru'
        assert resolve_language('video', 'AniLiberty', 'eng') == 'en'

    def test_subtitles_ru(self):
        assert resolve_language('subtitles', 'Надписи', '') == 'ru'
        assert resolve_language('subtitles', 'Субтитры', 'und') == 'ru'
        assert resolve_language('subtitles', '  Надписи  ', '') == 'ru'
        assert resolve_language('subtitles', '  Субтитры  ', 'und') == 'ru'

    def test_original_ja_when_empty(self):
        assert resolve_language('audio', 'Original', '') == 'ja'
        assert resolve_language('audio', 'Original', 'und') == 'ja'
        assert resolve_language('audio', 'Original', 'eng') == 'en'
        assert resolve_language('video', 'Original', 'unk') == 'ja'

    def test_compress_iso639(self):
        assert compress_language('rus') == 'ru'
        assert compress_language('jpn') == 'ja'
        assert compress_language('eng') == 'en'
        assert compress_language('und') is None
        assert compress_language('') is None


class TestBuildMkvmergeOptions:
    def test_skeleton_and_languages(self, tmp_path):
        encoded = tmp_path / 'enc.mkv'
        output = tmp_path / 'out.mkv'
        args = _build(encoded=str(encoded), output=str(output))

        assert args[:4] == [
            '--priority', 'lower',
            '--output', str(output.resolve()),
        ]
        assert '--ui-language' not in args
        assert '--no-track-tags' in args
        assert '--no-global-tags' in args
        assert '--disable-track-statistics-tags' not in args
        assert '(' in args and ')' in args
        assert str(encoded.resolve()) in args
        assert args[args.index('(') + 2] == ')'

        assert '0:ja' in _flag_values(args, '--language')
        assert '1:ru' in _flag_values(args, '--language')
        assert '2:ja' in _flag_values(args, '--language')
        assert '3:ru' in _flag_values(args, '--language')
        assert '4:ru' in _flag_values(args, '--language')
        assert '5:ru' in _flag_values(args, '--language')

        assert '--track-order' in args
        assert args[args.index('--track-order') + 1] == '0:0,0:1,0:2,0:3,0:4,0:5'

    def test_explicit_default_forced_original_flags(self):
        args = _build()
        defaults = _flag_values(args, '--default-track-flag')
        forced = _flag_values(args, '--forced-display-flag')
        original = _flag_values(args, '--original-flag')

        # единственное видео → default+forced
        assert '0:1' in defaults
        assert '0:1' in forced
        # аудио AniLiberty → default+forced
        assert '1:1' in defaults
        assert '1:1' in forced
        # субтитры Надписи → default+forced
        assert '3:1' in defaults
        assert '3:1' in forced
        # субтитры Субтитры → явно сбросить
        assert '4:0' in defaults
        assert '4:0' in forced
        # аудио Original → только original-flag
        assert '2:1' in original
        assert '2:1' not in defaults
        assert '2:1' not in forced
        assert '2:0' not in defaults
        assert '2:0' not in forced
        # видео Original → original-flag вместе с default+forced
        assert '0:1' in original
        # третья дорожка субтитров без default → «Субтитры»
        assert '5:0' in defaults
        assert '5:0' in forced
        assert not any(value.startswith('5:') for value in original)

    def test_aniliberty_flags_strip_title(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][1]['properties']['track_name'] = '  AniLiberty  '
        args = _build(identify=identify)
        defaults = _flag_values(args, '--default-track-flag')
        forced = _flag_values(args, '--forced-display-flag')
        assert '1:1' in defaults
        assert '1:1' in forced
        assert '1:ru' in _flag_values(args, '--language')

    def test_subtitle_titles_from_default_flag_not_source_name(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][3]['properties']['track_name'] = '  Signs  '
        identify['tracks'][3]['properties']['default_track'] = True
        identify['tracks'][4]['properties']['track_name'] = '  Full  '
        identify['tracks'][4]['properties']['default_track'] = False
        args = _build(identify=identify)
        names = _flag_values(args, '--track-name')
        defaults = _flag_values(args, '--default-track-flag')
        forced = _flag_values(args, '--forced-display-flag')
        langs = _flag_values(args, '--language')
        assert '3:Надписи' in names
        assert '4:Субтитры' in names
        assert '3:ru' in langs
        assert '4:ru' in langs
        assert '3:1' in defaults
        assert '3:1' in forced
        assert '4:0' in defaults
        assert '4:0' in forced
        assert '1:1' in defaults
        assert '2:1' in _flag_values(args, '--original-flag')
        assert '2:1' not in defaults
        assert '2:1' not in forced

    def test_no_video_flags_when_multiple_video_tracks(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'].insert(
            1,
            _track(
                6, 'video',
                codec='HEVC/H.265/MPEG-H',
                codec_id='V_MPEGH/ISO/HEVC',
                pixel_dimensions='1280x720',
                language='und',
                track_name='Alt',
            ),
        )
        args = _build(identify=identify)
        defaults = _flag_values(args, '--default-track-flag')
        forced = _flag_values(args, '--forced-display-flag')
        assert '0:1' not in defaults
        assert '0:1' not in forced
        assert '6:1' not in defaults
        assert '6:1' not in forced
        assert '1:1' in defaults
        assert '1:1' in forced
        assert '0:1' in _flag_values(args, '--original-flag')
        assert '6:1' not in _flag_values(args, '--original-flag')

    def test_single_default_subtitle_named_nadpisi(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'] = [
            identify['tracks'][0],
            _track(
                1, 'subtitles',
                codec='SubRip/SRT',
                codec_id='S_TEXT/UTF8',
                text_subtitles=True,
                language='und',
                track_name='Whatever',
                default_track=True,
            ),
        ]
        args = _build(identify=identify, nickname='')
        assert '1:Надписи' in _flag_values(args, '--track-name')
        assert '1:1' in _flag_values(args, '--default-track-flag')
        assert '1:1' in _flag_values(args, '--forced-display-flag')
        assert '1:ru' in _flag_values(args, '--language')

    def test_single_non_default_subtitle_named_subtitles(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'] = [
            identify['tracks'][0],
            _track(
                1, 'subtitles',
                codec='SubRip/SRT',
                codec_id='S_TEXT/UTF8',
                text_subtitles=True,
                language='jpn',
                track_name='Dialogue',
                default_track=False,
            ),
        ]
        args = _build(identify=identify, nickname='')
        assert '1:Субтитры' in _flag_values(args, '--track-name')
        assert '1:0' in _flag_values(args, '--default-track-flag')
        assert '1:0' in _flag_values(args, '--forced-display-flag')
        assert '1:ru' in _flag_values(args, '--language')

    def test_two_subtitles_swap_names_by_default_flag(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][3]['properties']['track_name'] = 'Full'
        identify['tracks'][3]['properties']['default_track'] = False
        identify['tracks'][4]['properties']['track_name'] = 'Signs'
        identify['tracks'][4]['properties']['default_track'] = True
        args = _build(identify=identify, nickname='')
        names = _flag_values(args, '--track-name')
        defaults = _flag_values(args, '--default-track-flag')
        assert '3:Субтитры' in names
        assert '4:Надписи' in names
        assert '3:0' in defaults
        assert '4:1' in defaults

    def test_two_subtitles_without_unique_default_use_order(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][3]['properties']['default_track'] = False
        identify['tracks'][4]['properties']['default_track'] = False
        args = _build(identify=identify, nickname='')
        names = _flag_values(args, '--track-name')
        assert '3:Надписи' in names
        assert '4:Субтитры' in names

    def test_no_subtitles_logs_and_skips_naming(self, caplog):
        identify = {
            'container': {'recognized': True, 'supported': True},
            'tracks': [
                _track(
                    0, 'video',
                    codec_id='V_MPEGH/ISO/HEVC',
                    pixel_dimensions='1920x1080',
                    language='jpn',
                    track_name='Original',
                ),
            ],
        }
        with caplog.at_level('INFO', logger='video_encoder'):
            args = _build(identify=identify, nickname='')
        assert 'Субтитры в файле отсутствуют' in caplog.text
        names = _flag_values(args, '--track-name')
        assert not any('Надписи' in name or 'Субтитры' in name for name in names)
        assert '0:1' in _flag_values(args, '--original-flag')

    def test_nickname_video_always_audio_only_original_when_encoded(self):
        copy_args = _build(audio_codec='copy', nickname='Nick')
        names = _flag_values(copy_args, '--track-name')
        assert '0:Original [Nick]' in names
        assert '1:AniLiberty' in names
        assert '2:Original' in names
        assert '3:Надписи' in names

        encoded_args = _build(audio_codec='libopus', nickname='Nick')
        encoded_names = _flag_values(encoded_args, '--track-name')
        assert '0:Original [Nick]' in encoded_names
        assert '2:Original [Nick]' in encoded_names
        assert encoded_names.count('0:Original [Nick]') == 1
        assert '1:AniLiberty' in encoded_names

    def test_does_not_duplicate_existing_nickname(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Original [Nick]'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        assert _flag_values(args, '--track-name').count('0:Original [Nick]') == 1
        assert '0:Original [Nick] [Nick]' not in _flag_values(args, '--track-name')

    def test_video_empty_title_becomes_bracket_nick(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = ''
        args = _build(identify=identify, nickname='Nick')
        assert '0:[Nick]' in _flag_values(args, '--track-name')

    def test_charset_only_for_text_subs(self):
        args = _build()
        charsets = _flag_values(args, '--sub-charset')
        assert '3:UTF-8' in charsets
        assert '4:UTF-8' in charsets
        assert not any(value.startswith('5:') for value in charsets)

    def test_color_only_when_present(self):
        with_color = _build(identify=_sample_identify(color=True))
        assert '0:1' in _flag_values(with_color, '--color-matrix-coefficients')
        assert '0:1' in _flag_values(with_color, '--color-range')
        assert '0:1' in _flag_values(with_color, '--color-transfer-characteristics')
        assert '0:1' in _flag_values(with_color, '--color-primaries')
        assert '0:1920x1080' in _flag_values(with_color, '--display-dimensions')

        without = _build(identify=_sample_identify(color=False))
        assert '--color-matrix-coefficients' not in without
        assert '--color-range' not in without
        assert '--color-primaries' not in without
        assert '0:1920x1080' in _flag_values(without, '--display-dimensions')

    def test_no_language_when_empty_and_no_rule(self):
        identify = {
            'container': {'recognized': True, 'supported': True},
            'tracks': [
                _track(
                    0, 'video',
                    codec_id='V_MPEGH/ISO/HEVC',
                    pixel_dimensions='1280x720',
                    language='',
                    track_name='Something',
                ),
            ],
        }
        args = _build(identify=identify, nickname='')
        assert '--language' not in args
        assert '0:Something' in _flag_values(args, '--track-name')

    def test_invalid_nickname_raises(self):
        with pytest.raises(ValueError, match='управляющие'):
            _build(nickname='bad\nx')

    def test_json_roundtrip(self, tmp_path):
        args = _build()
        dumped = json.loads(json.dumps(args, ensure_ascii=False))
        assert dumped == args
        assert all(isinstance(item, str) for item in dumped)


class TestIdentifyEncodedFile:
    def test_failure_raises(self):
        with patch(
            'app.mkvmerge_remux.subprocess.run',
        ) as mock_run, patch(
            'app.mkvmerge_remux.get_mkvmerge_bin',
            return_value='mkvmerge',
        ):
            mock_run.return_value.returncode = 2
            mock_run.return_value.stdout = ''
            mock_run.return_value.stderr = 'cannot open'
            with pytest.raises(RuntimeError, match='mkvmerge -J'):
                identify_encoded_file('/missing.mkv')

    def test_run_uses_utf8(self):
        payload = json.dumps({
            'container': {'recognized': True, 'supported': True},
            'tracks': [],
        })
        with patch(
            'app.mkvmerge_remux.subprocess.run',
        ) as mock_run, patch(
            'app.mkvmerge_remux.get_mkvmerge_bin',
            return_value='mkvmerge',
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = payload
            mock_run.return_value.stderr = ''
            identify_encoded_file('/ok.mkv')
            kwargs = mock_run.call_args.kwargs
            assert kwargs.get('encoding') == 'utf-8'
            assert kwargs.get('errors') == 'replace'
            assert kwargs.get('text') is True


class TestMkvmergeBinAndSidecars:
    def test_get_mkvmerge_bin_missing(self, monkeypatch):
        monkeypatch.delenv('MKVMERGE_BIN', raising=False)
        monkeypatch.setattr('app.mkvmerge_remux.shutil.which', lambda _name: None)
        with pytest.raises(RuntimeError, match='mkvmerge не найден'):
            get_mkvmerge_bin()

    def test_get_mkvmerge_bin_configured_missing_file(self, monkeypatch, tmp_path):
        missing = tmp_path / 'no-such-mkvmerge'
        monkeypatch.setenv('MKVMERGE_BIN', str(missing))
        monkeypatch.setattr('app.mkvmerge_remux.shutil.which', lambda _name: None)
        with pytest.raises(RuntimeError, match='mkvmerge не найден'):
            get_mkvmerge_bin()

    def test_cleanup_job_sidecars(self, tmp_path):
        output = tmp_path / 'out.mkv'
        output.write_bytes(b'final')
        job_id = 'abc12345-job'
        paths = job_sidecar_paths(str(output), job_id)
        assert any(item.endswith('.partial.mkv') for item in paths)
        assert any(item.endswith('.remux.tmp') for item in paths)
        assert any(item.endswith('.mkvmerge.json') for item in paths)

        created = []
        for path in paths:
            handle = Path(path)
            handle.write_bytes(b'x')
            created.append(handle)

        cleanup_job_sidecars(str(output), job_id)
        assert output.exists()
        for handle in created:
            assert not handle.exists()
