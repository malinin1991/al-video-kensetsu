"""Тесты JSON-опций mkvmerge: языки, ник, charset."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.mkvmerge_remux import (
    MIME_OPENTYPE,
    MIME_TRUETYPE,
    build_mkvmerge_options,
    build_mkvpropedit_font_mime_command,
    cleanup_job_sidecars,
    collect_font_mime_fixes,
    compress_language,
    escape_nickname_for_tag,
    expected_font_mime_type,
    fix_font_attachment_mime_types,
    get_mkvmerge_bin,
    get_mkvpropedit_bin,
    identify_encoded_file,
    is_aniliberty_title,
    is_original_track_title,
    job_sidecar_paths,
    resolve_language,
    sanitize_nickname,
    sanitize_video_title,
    unescape_nickname_tag,
)


def _track(tid: int, track_type: str, **props):
    return {
        'id': tid,
        'type': track_type,
        'codec': props.pop('codec', 'unknown'),
        'properties': props,
    }


def _sample_identify(*, pgs=True) -> dict:
    video_props = {
        'codec_id': 'V_MPEGH/ISO/HEVC',
        'pixel_dimensions': '1920x1080',
        'display_dimensions': '1920x1080',
        'language': 'und',
        'track_name': 'Original',
        'color_matrix_coefficients': 1,
        'color_range': 1,
        'color_transfer_characteristics': 1,
        'color_primaries': 1,
    }

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


def _build(
    identify=None,
    nickname='Nick',
    video_title='',
    audio_codec='copy',
    encoded='/enc.mkv',
    output='/out.mkv',
):
    return build_mkvmerge_options(
        encoded,
        output,
        identify if identify is not None else _sample_identify(),
        {
            'metadata_nickname': nickname,
            'metadata_video_title': video_title,
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
        assert sanitize_nickname('Nick+AL') == 'Nick+AL'
        assert sanitize_nickname('') == ''

    def test_rejects_control_and_too_long(self):
        with pytest.raises(ValueError, match='управляющие'):
            sanitize_nickname('bad\nnick')
        with pytest.raises(ValueError, match='длинный'):
            sanitize_nickname('x' * 65)

    def test_sanitize_video_title(self):
        assert sanitize_video_title(' Original ') == 'Original'
        assert sanitize_video_title('') == ''
        with pytest.raises(ValueError, match='управляющие'):
            sanitize_video_title('bad\ntitle')
        with pytest.raises(ValueError, match='длинное'):
            sanitize_video_title('x' * 65)

    def test_escape_structural_chars(self):
        assert escape_nickname_for_tag('Nick') == 'Nick'
        assert escape_nickname_for_tag('Ник [AL]') == r'Ник \[AL\]'
        assert escape_nickname_for_tag('Nick+AL') == r'Nick\+AL'
        assert escape_nickname_for_tag(r'a\b') == r'a\\b'
        assert escape_nickname_for_tag('[Anim,ad') == r'\[Anim,ad'
        assert escape_nickname_for_tag('[[Anim,ad]') == r'\[\[Anim,ad\]'
        assert escape_nickname_for_tag('Nick + AL') == r'Nick \+ AL'
        assert escape_nickname_for_tag('a+b+c') == r'a\+b\+c'
        assert escape_nickname_for_tag('HDR10+') == r'HDR10\+'

    def test_unescape_roundtrip(self):
        cases = (
            'Nick',
            r'Ник \[AL\]',
            r'Nick\+AL',
            r'a\\b',
            r'HDR10\+',
            r'Nick \+ AL',
        )
        for escaped in cases:
            assert escape_nickname_for_tag(unescape_nickname_tag(escaped)) == escaped
        assert unescape_nickname_tag(r'HDR10\+') == 'HDR10+'
        assert unescape_nickname_tag(r'Ник \[AL\]') == 'Ник [AL]'

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

        assert args[:2] == [
            '--output', str(output.resolve()),
        ]
        assert '--priority' not in args
        assert '--ui-language' not in args
        assert '--no-track-tags' in args
        assert '--no-global-tags' in args
        assert '--enable-legacy-font-mime-types' in args
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

    def test_original_becomes_original_bracket_nickname(self):
        """Без []: Title → Title [NickName] (имя дорожки не важно)."""
        for title in ('Original', 'AniLiberty', 'Something Else'):
            identify = _sample_identify()
            identify['tracks'][0]['properties']['track_name'] = title
            args = _build(identify=identify, nickname='NickName', audio_codec='copy')
            names = _flag_values(args, '--track-name')
            expected = f'0:{title} [NickName]'
            assert expected in names
            assert not any(name.startswith(f'0:{title} [NickName +') for name in names)
            assert names.count(expected) == 1

    def test_does_not_duplicate_existing_nickname(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Original [Nick]'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        assert _flag_values(args, '--track-name').count('0:Original [Nick]') == 1
        assert '0:Original [Nick] [Nick]' not in _flag_values(args, '--track-name')

    def test_video_merges_nickname_into_existing_brackets(self):
        """С []: вклеиваем внутрь, независимо от base-имени."""
        for title in ('AniLiberty [Nokt]', 'Original [Nokt]', 'Custom [Encoder]'):
            identify = _sample_identify()
            identify['tracks'][0]['properties']['track_name'] = title
            base, _, tag = title.partition(' [')
            tag = tag.rstrip(']')
            args = _build(identify=identify, nickname='Nick', audio_codec='copy')
            names = _flag_values(args, '--track-name')
            assert f'0:{base} [Nick + {tag}]' in names
            assert f'0:{title} [Nick]' not in names

    def test_video_does_not_duplicate_nick_already_in_merged_tag(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'AniLiberty [Nick + Nokt]'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert names.count('0:AniLiberty [Nick + Nokt]') == 1
        assert '0:AniLiberty [Nick + Nick + Nokt]' not in names

    def test_original_plus_geekaz0id_nickname(self):
        """Название Original + ник GeeKaZ0iD → Original [GeeKaZ0iD]."""
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Original'
        args = _build(identify=identify, nickname='GeeKaZ0iD', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert '0:Original [GeeKaZ0iD]' in names
        assert names.count('0:Original [GeeKaZ0iD]') == 1
        assert '0:1' in _flag_values(args, '--original-flag')

    def test_video_empty_title_becomes_original_with_nick(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = ''
        args = _build(identify=identify, nickname='Nick')
        names = _flag_values(args, '--track-name')
        assert '0:Original [Nick]' in names
        assert '0:[Nick]' not in names
        assert '0:1' in _flag_values(args, '--original-flag')

    def test_video_empty_title_without_nickname_is_original(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = ''
        args = _build(identify=identify, nickname='')
        names = _flag_values(args, '--track-name')
        assert '0:Original' in names
        assert '0:1' in _flag_values(args, '--original-flag')

    def test_video_title_override_replaces_base(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'ReleaseGroup'
        args = _build(
            identify=identify,
            nickname='GeeKaZ0iD',
            video_title='Original',
            audio_codec='copy',
        )
        names = _flag_values(args, '--track-name')
        assert '0:Original [GeeKaZ0iD]' in names
        assert '0:ReleaseGroup [GeeKaZ0iD]' not in names
        assert '0:1' in _flag_values(args, '--original-flag')

    def test_video_title_override_keeps_trailing_brackets(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'ReleaseGroup [Encoder]'
        args = _build(
            identify=identify,
            nickname='Nick',
            video_title='Original',
            audio_codec='copy',
        )
        names = _flag_values(args, '--track-name')
        assert '0:Original [Nick + Encoder]' in names
        assert '0:ReleaseGroup [Nick + Encoder]' not in names

    def test_video_title_override_ignored_when_multiple_videos(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][0]['properties']['track_name'] = 'ReleaseGroup'
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
        args = _build(
            identify=identify,
            nickname='Nick',
            video_title='Original',
            audio_codec='copy',
        )
        names = _flag_values(args, '--track-name')
        assert '0:ReleaseGroup [Nick]' in names
        assert '6:Alt [Nick]' in names
        assert '0:Original [Nick]' not in names
        assert '6:Original [Nick]' not in names

    def test_video_empty_still_original_with_multiple_videos(self):
        identify = _sample_identify(pgs=False)
        identify['tracks'][0]['properties']['track_name'] = ''
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
        args = _build(identify=identify, nickname='Nick', video_title='Ignored')
        names = _flag_values(args, '--track-name')
        assert '0:Original [Nick]' in names
        assert '6:Alt [Nick]' in names

    def test_video_escapes_structural_chars_in_nickname(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'AniLiberty [Nokt]'
        args = _build(identify=identify, nickname='Ник [AL]', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert r'0:AniLiberty [Ник \[AL\] + Nokt]' in names
        assert '0:AniLiberty [Ник [AL] + Nokt]' not in names

        # Идемпотентность после экранирования
        identify['tracks'][0]['properties']['track_name'] = r'AniLiberty [Ник \[AL\] + Nokt]'
        again = _build(identify=identify, nickname='Ник [AL]', audio_codec='copy')
        again_names = _flag_values(again, '--track-name')
        assert again_names.count(r'0:AniLiberty [Ник \[AL\] + Nokt]') == 1

        plain = _sample_identify()
        plain['tracks'][0]['properties']['track_name'] = 'Original'
        plain_args = _build(identify=plain, nickname='Ник [AL]', audio_codec='copy')
        assert r'0:Original [Ник \[AL\]]' in _flag_values(plain_args, '--track-name')

    def test_video_weird_bracket_nicknames_do_not_break(self):
        """Ники с [, ], + (в т.ч. [Anim,ad / [[Anim,ad]) не ломают разбор и идемпотентность."""
        cases = (
            ('[Anim,ad', r'\[Anim,ad'),
            ('[[Anim,ad]', r'\[\[Anim,ad\]'),
            ('Anim]ad[', r'Anim\]ad\['),
            ('[Anim+ad]', r'\[Anim\+ad\]'),
            ('Anim+ad', r'Anim\+ad'),
            ('Nick + AL', r'Nick \+ AL'),
            ('+prefix', r'\+prefix'),
            ('tail+', r'tail\+'),
            ('a+b+c', r'a\+b\+c'),
            ('[Anim,ad]+x', r'\[Anim,ad\]\+x'),
        )
        for nickname, escaped in cases:
            assert sanitize_nickname(nickname) == nickname
            assert escape_nickname_for_tag(nickname) == escaped

            # без [] → Title [escaped]
            for title in ('Original', 'AniLiberty'):
                identify = _sample_identify()
                identify['tracks'][0]['properties']['track_name'] = title
                args = _build(identify=identify, nickname=nickname, audio_codec='copy')
                names = _flag_values(args, '--track-name')
                expected = f'0:{title} [{escaped}]'
                assert expected in names, nickname
                # сырой ник без escape не должен попасть в название (если он отличается)
                if nickname != escaped:
                    assert f'0:{title} [{nickname}]' not in names

            # с [] → Title [escaped + Existing]
            identify = _sample_identify()
            identify['tracks'][0]['properties']['track_name'] = 'AniLiberty [Nokt]'
            merged_args = _build(identify=identify, nickname=nickname, audio_codec='copy')
            merged_names = _flag_values(merged_args, '--track-name')
            merged = f'0:AniLiberty [{escaped} + Nokt]'
            assert merged in merged_names, nickname
            assert f'0:AniLiberty [Nokt] [{nickname}]' not in merged_names
            if nickname != escaped:
                assert f'0:AniLiberty [{nickname} + Nokt]' not in merged_names

            # повторный прогон не дублирует (в т.ч. когда в нике есть « + »)
            identify['tracks'][0]['properties']['track_name'] = f'AniLiberty [{escaped} + Nokt]'
            again = _build(identify=identify, nickname=nickname, audio_codec='copy')
            assert _flag_values(again, '--track-name').count(merged) == 1
            assert f'0:AniLiberty [{escaped} + {escaped} + Nokt]' not in _flag_values(
                again, '--track-name'
            )

    def test_video_empty_brackets_become_nick(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Title []'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert '0:Title [Nick]' in names
        assert '0:Title [Nick + ]' not in names

    def test_video_crazy_nested_brackets_merge_verbatim(self):
        nested = r'AniLiberty [d[][]][][][[[[]]]d[]][]/\]]]'
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = nested
        args = _build(identify=identify, nickname='NickName', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        expected = r'0:AniLiberty [NickName + d[][]][][][[[[]]]d[]][]/\]]]'
        assert expected in names

        identify['tracks'][0]['properties']['track_name'] = expected[2:]  # без '0:'
        again = _build(identify=identify, nickname='NickName', audio_codec='copy')
        assert _flag_values(again, '--track-name').count(expected) == 1
        assert r'0:AniLiberty [NickName + NickName + d[][]][][][[[[]]]d[]][]/\]]]' not in (
            _flag_values(again, '--track-name')
        )

    def test_video_hdr10_plus_tag_idempotent_with_raw_plus_nick(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Title [HDR10+]'
        args = _build(identify=identify, nickname='HDR10+', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert names.count('0:Title [HDR10+]') == 1
        assert r'0:Title [HDR10\+ + HDR10+]' not in names
        assert r'0:Title [HDR10\+]' not in names

    def test_video_legacy_nested_nick_idempotent_and_merge(self):
        legacy = 'Original [Ник [AL]]'
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = legacy
        same = _build(identify=identify, nickname='Ник [AL]', audio_codec='copy')
        assert _flag_values(same, '--track-name').count(f'0:{legacy}') == 1
        assert r'0:Original [Ник \[AL\] + Ник [AL]]' not in _flag_values(same, '--track-name')

        other = _build(identify=identify, nickname='Other', audio_codec='copy')
        assert '0:Original [Other + Ник [AL]]' in _flag_values(other, '--track-name')

        special = _build(identify=identify, nickname='Ник [X]', audio_codec='copy')
        assert r'0:Original [Ник \[X\] + Ник [AL]]' in _flag_values(special, '--track-name')

    def test_video_uses_last_outer_bracket_pair_at_eos(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Title [A] [B]'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        names = _flag_values(args, '--track-name')
        assert '0:Title [A] [Nick + B]' in names
        assert '0:Title [Nick + A] [B]' not in names

    def test_video_unbalanced_closing_bracket_appends_suffix(self):
        identify = _sample_identify()
        identify['tracks'][0]['properties']['track_name'] = 'Title ]'
        args = _build(identify=identify, nickname='Nick', audio_codec='copy')
        assert '0:Title ] [Nick]' in _flag_values(args, '--track-name')

    def test_audio_still_appends_suffix_without_merge(self):
        identify = _sample_identify()
        identify['tracks'][2]['properties']['track_name'] = 'Original [JP]'
        args = _build(identify=identify, nickname='Nick', audio_codec='libopus')
        names = _flag_values(args, '--track-name')
        assert '2:Original [JP] [Nick]' in names
        assert '2:Original [Nick + JP]' not in names

    def test_audio_escapes_special_nick_and_idempotent_raw_or_escaped(self):
        identify = _sample_identify()
        identify['tracks'][2]['properties']['track_name'] = 'Original'
        args = _build(identify=identify, nickname='Ник [AL]', audio_codec='libopus')
        names = _flag_values(args, '--track-name')
        assert r'2:Original [Ник \[AL\]]' in names
        assert '2:Original [Ник [AL]]' not in names

        identify['tracks'][2]['properties']['track_name'] = r'Original [Ник \[AL\]]'
        again = _build(identify=identify, nickname='Ник [AL]', audio_codec='libopus')
        assert _flag_values(again, '--track-name').count(r'2:Original [Ник \[AL\]]') == 1

        identify['tracks'][2]['properties']['track_name'] = 'Original [Ник [AL]]'
        raw_again = _build(identify=identify, nickname='Ник [AL]', audio_codec='libopus')
        assert _flag_values(raw_again, '--track-name').count('2:Original [Ник [AL]]') == 1
        assert r'2:Original [Ник [AL]] [Ник \[AL\]]' not in _flag_values(
            raw_again, '--track-name'
        )

    def test_charset_only_for_text_subs(self):
        args = _build()
        charsets = _flag_values(args, '--sub-charset')
        assert '3:UTF-8' in charsets
        assert '4:UTF-8' in charsets
        assert not any(value.startswith('5:') for value in charsets)

    def test_color_and_dimensions_not_forced(self):
        """Цвет/размер из encode копирует mkvmerge сам — флаги не ставим."""
        args = _build(identify=_sample_identify())
        assert '--color-matrix-coefficients' not in args
        assert '--color-range' not in args
        assert '--color-transfer-characteristics' not in args
        assert '--color-primaries' not in args
        assert '--display-dimensions' not in args

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


class TestFontMimeFixes:
    def test_expected_font_mime_type_by_extension(self):
        assert expected_font_mime_type('9312.TTF') == MIME_TRUETYPE
        assert expected_font_mime_type('fonts.ttc') == MIME_TRUETYPE
        assert expected_font_mime_type('A-OTF-ShinGoPr5-Bold.otf') == MIME_OPENTYPE
        assert expected_font_mime_type('readme.txt') is None
        assert expected_font_mime_type('') is None

    def test_collect_font_mime_fixes_skips_ok_and_non_fonts(self):
        identify = {
            'attachments': [
                {
                    'id': 1,
                    'file_name': 'CAPTURE IT.TTF',
                    'content_type': 'application/x-font-ttf',
                },
                {
                    'id': 2,
                    'file_name': 'BrushaffMod.ttf',
                    'content_type': MIME_TRUETYPE,
                },
                {
                    'id': 3,
                    'file_name': 'A-OTF-ShinGoPr5-Bold.otf',
                    'content_type': MIME_OPENTYPE,
                },
                {
                    'id': 4,
                    'file_name': 'wrong.otf',
                    'content_type': 'font/otf',
                },
                {
                    'id': 5,
                    'file_name': 'note.txt',
                    'content_type': 'text/plain',
                },
            ]
        }
        fixes = collect_font_mime_fixes(identify)
        assert [(f['id'], f['expected_mime']) for f in fixes] == [
            (1, MIME_TRUETYPE),
            (4, MIME_OPENTYPE),
        ]
        assert fixes[0]['current_mime'] == 'application/x-font-ttf'

    def test_build_mkvpropedit_command(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            'app.mkvmerge_remux.get_mkvpropedit_bin',
            lambda: 'mkvpropedit',
        )
        target = tmp_path / 'out.partial.mkv'
        target.write_bytes(b'x')
        cmd = build_mkvpropedit_font_mime_command(
            str(target),
            [
                {
                    'id': 1,
                    'expected_mime': MIME_TRUETYPE,
                },
                {
                    'id': 4,
                    'expected_mime': MIME_OPENTYPE,
                },
            ],
        )
        assert cmd[0] == 'mkvpropedit'
        assert cmd[1] == str(target.resolve())
        assert cmd[2:] == [
            '--attachment-mime-type',
            MIME_TRUETYPE,
            '--update-attachment',
            '1',
            '--attachment-mime-type',
            MIME_OPENTYPE,
            '--update-attachment',
            '4',
        ]

    def test_fix_font_attachment_mime_types_runs_propedit(
        self, monkeypatch, tmp_path
    ):
        target = tmp_path / 'out.partial.mkv'
        target.write_bytes(b'x')
        identify = {
            'container': {'recognized': True, 'supported': True},
            'attachments': [
                {
                    'id': 7,
                    'file_name': 'ROMC.TTF',
                    'content_type': 'application/x-font-ttf',
                },
            ],
        }
        monkeypatch.setattr(
            'app.mkvmerge_remux.get_mkvpropedit_bin',
            lambda: 'mkvpropedit',
        )

        class Result:
            returncode = 0
            stdout = 'Done.'
            stderr = ''

        with patch(
            'app.mkvmerge_remux.subprocess.run', return_value=Result()
        ) as run:
            fixes = fix_font_attachment_mime_types(
                str(target), identify=identify
            )
            run.assert_called_once()
            cmd = run.call_args[0][0]
            assert cmd[0] == 'mkvpropedit'
            assert '--update-attachment' in cmd
            assert '7' in cmd
            assert MIME_TRUETYPE in cmd

        assert len(fixes) == 1
        assert fixes[0]['id'] == 7

    def test_fix_font_attachment_mime_types_noop_when_ok(
        self, monkeypatch, tmp_path
    ):
        target = tmp_path / 'out.partial.mkv'
        target.write_bytes(b'x')
        identify = {
            'attachments': [
                {
                    'id': 1,
                    'file_name': 'OpenSans-Semibold.ttf',
                    'content_type': MIME_TRUETYPE,
                },
            ],
        }
        with patch('app.mkvmerge_remux.subprocess.run') as run:
            fixes = fix_font_attachment_mime_types(
                str(target), identify=identify
            )
            run.assert_not_called()
        assert fixes == []

    def test_get_mkvpropedit_bin_missing(self, monkeypatch):
        monkeypatch.delenv('MKVPROPEDIT_BIN', raising=False)
        monkeypatch.setattr('app.mkvmerge_remux.shutil.which', lambda _name: None)
        with pytest.raises(RuntimeError, match='mkvpropedit не найден'):
            get_mkvpropedit_bin()


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
