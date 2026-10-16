"""Резолв слоёв video / audio / subtitles / data."""

from __future__ import annotations

import pytest
import yaml

from app.models.presets import PresetManager, preset_manager


def _video_yaml(**overrides):
    """Полный video-пресет для тестов (без опоры на код-дефолты)."""
    payload = {
        'id': 'hevc_rip',
        'name': 'V',
        'description': 'test',
        'default': True,
        'video_codec': 'libx265',
        'preset': 'slow',
        'crf': '23',
        'pixel_format': 'yuv420p10le',
        'x265_params': 'level=5.1:ref=6',
        'tune': 'animation',
        'output_format': 'matroska',
        'video_map': '0:v:0',
    }
    payload.update(overrides)
    return payload


class TestPresetsResolve:
    def test_loaded_layers_and_defaults(self):
        assert set(preset_manager.videos.keys()) == {
            'hevc_rip', 'hevc_remux', 'hevc_auto_level_rip',
        }
        assert set(preset_manager.audios.keys()) == {
            'copy', 'opus', 'aac', 'aac_fdk', 'none',
        }
        assert set(preset_manager.subtitles.keys()) == {'copy', 'none'}
        assert set(preset_manager.data.keys()) == {'copy', 'none'}
        assert preset_manager.default_video_id == 'hevc_auto_level_rip'
        assert preset_manager.default_audio_id == 'copy'
        assert preset_manager.default_subtitles_id == 'copy'
        assert preset_manager.default_data_id == 'copy'
        auto = preset_manager.get_video('hevc_auto_level_rip')
        assert auto.x265_params is None
        assert auto.preset == 'slow'

    def test_resolve_defaults(self):
        params = preset_manager.resolve()
        assert params['preset'] == 'slow'
        assert params['crf'] == '23'
        assert params['x265_params'] is None
        assert params['audio_codec'] == 'copy'
        assert params['subtitle_map'] == '0:s?'
        assert params['data_map'] == '0:t?'
        assert params['audio_downmix'] == 'none'

    def test_resolve_hevc_remux_opus(self):
        params = preset_manager.resolve('hevc_remux', 'opus')
        assert params['preset'] == 'slower'
        assert params['crf'] == '21'
        assert params['audio_codec'] == 'libopus'
        assert params['audio_bitrate'] == '160k'
        assert params['subtitle_codec'] == 'copy'
        assert params['data_codec'] == 'copy'

    def test_audio_none_forces_subs_data_none(self):
        params = preset_manager.resolve('hevc_rip', 'none')
        assert params['audio_codec'] is None
        assert params['audio_map'] is None
        assert params['subtitle_map'] is None
        assert params['data_map'] is None
        assert params['subtitle_codec'] is None
        assert params['data_codec'] is None

    def test_audio_none_raises_without_subs_none_preset(self, tmp_path):
        root = tmp_path / 'presets'
        for layer, files in {
            'video': [
                ('hevc_rip.yaml', _video_yaml()),
            ],
            'audio': [
                ('none.yaml', {
                    'id': 'none', 'name': 'No audio', 'default': True,
                    'codec': None, 'bitrate': None, 'audio_map': None,
                }),
            ],
            'subtitles': [
                ('copy.yaml', {
                    'id': 'copy', 'name': 'Copy', 'default': True,
                }),
            ],
            'data': [
                ('copy.yaml', {
                    'id': 'copy', 'name': 'Copy', 'default': True,
                }),
                ('none.yaml', {
                    'id': 'none', 'name': 'None', 'default': False,
                    'data_map': None, 'data_codec': None,
                }),
            ],
        }.items():
            d = root / layer
            d.mkdir(parents=True)
            for name, payload in files:
                (d / name).write_text(yaml.dump(payload), encoding='utf-8')

        manager = PresetManager(presets_dir=str(root))
        with pytest.raises(ValueError, match='subtitles_id=none'):
            manager.resolve('hevc_rip', 'none')

    def test_audio_none_raises_without_data_none_preset(self, tmp_path):
        root = tmp_path / 'presets'
        for layer, files in {
            'video': [
                ('hevc_rip.yaml', _video_yaml()),
            ],
            'audio': [
                ('none.yaml', {
                    'id': 'none', 'name': 'No audio', 'default': True,
                    'codec': None, 'bitrate': None, 'audio_map': None,
                }),
            ],
            'subtitles': [
                ('copy.yaml', {
                    'id': 'copy', 'name': 'Copy', 'default': True,
                }),
                ('none.yaml', {
                    'id': 'none', 'name': 'None', 'default': False,
                    'subtitle_map': None, 'subtitle_codec': None,
                }),
            ],
            'data': [
                ('copy.yaml', {
                    'id': 'copy', 'name': 'Copy', 'default': True,
                }),
            ],
        }.items():
            d = root / layer
            d.mkdir(parents=True)
            for name, payload in files:
                (d / name).write_text(yaml.dump(payload), encoding='utf-8')

        manager = PresetManager(presets_dir=str(root))
        with pytest.raises(ValueError, match='data_id=none'):
            manager.resolve('hevc_rip', 'none')

    def test_stereo_copy_rejected(self):
        with pytest.raises(ValueError, match='copy'):
            preset_manager.resolve('hevc_rip', 'copy', 'stereo')

    def test_stereo_none_rejected(self):
        with pytest.raises(ValueError, match='none|без аудио'):
            preset_manager.resolve('hevc_rip', 'none', 'stereo')

    def test_stereo_opus_ok(self):
        params = preset_manager.resolve('hevc_rip', 'opus', 'stereo')
        assert params['audio_downmix'] == 'stereo'

    def test_unknown_video(self):
        with pytest.raises(ValueError, match='video_id'):
            preset_manager.resolve('av1_rip', 'copy')

    def test_unknown_audio(self):
        with pytest.raises(ValueError, match='audio_id'):
            preset_manager.resolve('hevc_rip', 'flac')

    def test_display_label(self):
        from app.models.presets import display_label

        video = preset_manager.get_video('hevc_remux')
        audio = preset_manager.get_audio('opus')
        assert display_label(video, audio) == 'HEVC remux / Opus'
        assert 'stereo downmix' in display_label(video, audio, 'stereo')

    def test_optional_encoding_keys_ok(self, tmp_path):
        root = tmp_path / 'presets'
        video_dir = root / 'video'
        video_dir.mkdir(parents=True)
        (video_dir / 'minimal.yaml').write_text(
            yaml.dump({
                'id': 'minimal',
                'name': 'Minimal',
                'default': True,
                'video_codec': 'libx265',
                'output_format': 'matroska',
            }),
            encoding='utf-8',
        )
        manager = PresetManager(presets_dir=str(root))
        video = manager.get_video('minimal')
        assert video.video_codec == 'libx265'
        assert video.output_format == 'matroska'
        assert video.preset is None
        assert video.crf is None
        assert video.x265_params is None
        assert video.video_map is None
        dumped = video.to_dict()
        assert 'x265_params' not in dumped
        assert 'video_map' not in dumped
        assert dumped['video_codec'] == 'libx265'

    def test_missing_video_codec_raises(self, tmp_path):
        root = tmp_path / 'presets'
        video_dir = root / 'video'
        video_dir.mkdir(parents=True)
        (video_dir / 'broken.yaml').write_text(
            yaml.dump({
                'id': 'broken',
                'name': 'Broken',
                'default': True,
                'output_format': 'matroska',
            }),
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='video_codec'):
            PresetManager(presets_dir=str(root))

    def test_yaml_python_tags_remain_rejected(self, tmp_path):
        directory = tmp_path / 'presets' / 'video'
        directory.mkdir(parents=True)
        (directory / 'unsafe.yaml').write_text(
            '!!python/object/apply:builtins.str [not-a-preset]', encoding='utf-8',
        )
        with pytest.raises(yaml.constructor.ConstructorError):
            PresetManager(presets_dir=str(directory.parent))

    def test_incomplete_video_yaml_raises(self, tmp_path):
        root = tmp_path / 'presets'
        video_dir = root / 'video'
        video_dir.mkdir(parents=True)
        (video_dir / 'broken.yaml').write_text(
            yaml.dump({'id': 'broken', 'name': 'Broken', 'default': True}),
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='обязательное поле'):
            PresetManager(presets_dir=str(root))

    def test_exactly_one_default_per_layer(self, tmp_path):
        root = tmp_path / 'presets'
        for layer, files in {
            'video': [
                ('hevc_rip.yaml', _video_yaml(id='hevc_rip', name='A', default=True)),
                ('hevc_remux.yaml', _video_yaml(
                    id='hevc_remux', name='B', default=False, preset='slower', crf='21',
                )),
            ],
            'audio': [('copy.yaml', {'id': 'copy', 'name': 'C', 'default': True, 'codec': 'copy'})],
            'subtitles': [('copy.yaml', {'id': 'copy', 'name': 'S', 'default': True}),
                          ('none.yaml', {'id': 'none', 'name': 'N', 'default': False,
                                         'subtitle_map': None, 'subtitle_codec': None})],
            'data': [('copy.yaml', {'id': 'copy', 'name': 'D', 'default': True}),
                     ('none.yaml', {'id': 'none', 'name': 'N', 'default': False,
                                    'data_map': None, 'data_codec': None})],
        }.items():
            d = root / layer
            d.mkdir(parents=True)
            for name, payload in files:
                (d / name).write_text(yaml.dump(payload), encoding='utf-8')

        manager = PresetManager(presets_dir=str(root))
        assert manager.default_video_id == 'hevc_rip'
        assert manager.default_audio_id == 'copy'

        # два default на video → ValueError
        bad = yaml.safe_load((root / 'video' / 'hevc_remux.yaml').read_text())
        bad['default'] = True
        (root / 'video' / 'hevc_remux.yaml').write_text(yaml.dump(bad), encoding='utf-8')
        with pytest.raises(ValueError, match='default'):
            PresetManager(presets_dir=str(root))

    def test_create_default_presets_tmp(self, tmp_path):
        manager = PresetManager(presets_dir=str(tmp_path / 'presets'))
        assert manager.default_video_id == 'hevc_auto_level_rip'
        assert manager.resolve()['x265_params'] is None
        auto = yaml.safe_load(
            (tmp_path / 'presets' / 'video' / 'hevc_auto_level_rip.yaml').read_text(encoding='utf-8')
        )
        assert auto['default'] is True
        assert 'x265_params' not in auto
        # При повторной загрузке с диска сохраняются те же defaults и три пресета.
        reloaded = PresetManager(presets_dir=manager.presets_dir)
        assert reloaded.default_video_id == 'hevc_auto_level_rip'
        assert set(reloaded.videos) == set(preset_manager.videos)
        assert (tmp_path / 'presets' / 'video' / 'hevc_rip.yaml').exists()
        assert (tmp_path / 'presets' / 'subtitles' / 'copy.yaml').exists()
        assert (tmp_path / 'presets' / 'data' / 'copy.yaml').exists()
        assert not (tmp_path / 'presets' / 'tracks').exists()
        rip = yaml.safe_load(
            (tmp_path / 'presets' / 'video' / 'hevc_rip.yaml').read_text(encoding='utf-8')
        )
        assert rip['x265_params'] == 'level=5.1:ref=6'
        assert rip['pixel_format'] == 'yuv420p10le'
        assert rip['default'] is False

    def test_missing_data_layer_does_not_wipe_others(self, tmp_path):
        root = tmp_path / 'presets'
        for layer, files in {
            'video': [
                (
                    'custom_rip.yaml',
                    _video_yaml(
                        id='custom_rip',
                        name='Custom rip',
                        default=True,
                        crf='18',
                        preset='medium',
                    ),
                ),
            ],
            'audio': [
                (
                    'copy.yaml',
                    {
                        'id': 'copy',
                        'name': 'Copy',
                        'default': True,
                        'codec': 'copy',
                    },
                ),
            ],
            'subtitles': [
                (
                    'copy.yaml',
                    {
                        'id': 'copy',
                        'name': 'Copy',
                        'default': True,
                    },
                ),
            ],
        }.items():
            d = root / layer
            d.mkdir(parents=True)
            for name, payload in files:
                (d / name).write_text(yaml.dump(payload), encoding='utf-8')

        # data/ отсутствует — должен создаться только он
        manager = PresetManager(presets_dir=str(root))
        assert set(manager.videos.keys()) == {'custom_rip'}
        assert manager.videos['custom_rip'].crf == '18'
        assert set(manager.data.keys()) == {'copy', 'none'}
        assert (root / 'data' / 'copy.yaml').exists()
        assert not (root / 'video' / 'hevc_rip.yaml').exists()
        custom_on_disk = yaml.safe_load(
            (root / 'video' / 'custom_rip.yaml').read_text(encoding='utf-8')
        )
        assert custom_on_disk['crf'] == '18'
        assert custom_on_disk['id'] == 'custom_rip'
