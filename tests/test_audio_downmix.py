"""Selective stereo downmix: pan только для channels > 2."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.audio_downmix import (
    ATSC_LORO_PAN,
    AudioDownmixError,
    AudioStreamInfo,
    build_downmix_plan,
    needs_downmix,
    parse_audio_map,
    probe_audio_streams,
)
from app.ffmpeg_worker import FFmpegWorker
from app.models.job import Job
from app.models.presets import validate_audio_downmix_for_params


class TestNeedsDownmix:
    def test_stereo_and_mono_skip(self):
        assert needs_downmix(1) is False
        assert needs_downmix(2) is False

    def test_surround_needs(self):
        assert needs_downmix(6) is True
        assert needs_downmix(8) is True


class TestParseAudioMap:
    def test_all_and_index(self):
        assert parse_audio_map('0:a?').mode == 'all'
        spec = parse_audio_map('0:a:1')
        assert spec.mode == 'index'
        assert spec.index == 1

    def test_exotic_rejected(self):
        with pytest.raises(AudioDownmixError):
            parse_audio_map('0:a:eng')


class TestValidateStereoAudioMapEarly:
    def test_bad_map_rejected(self):
        with pytest.raises(ValueError, match='audio_map'):
            validate_audio_downmix_for_params(
                {'audio_codec': 'libopus', 'audio_map': '0:a:eng'},
                'stereo',
            )

    def test_good_map_ok(self):
        assert validate_audio_downmix_for_params(
            {'audio_codec': 'libopus', 'audio_map': '0:a:0'},
            'stereo',
        ) == 'stereo'


class TestJobDownmixConsistency:
    def test_params_forced_to_job_downmix(self, tmp_path):
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        job = Job(
            id='job-sync',
            filename='in.mkv',
            original_path=str(src),
            video_id='hevc_rip',
            audio_id='opus',
            audio_downmix='stereo',
            ffmpeg_params={
                'video_map': '0:v:0',
                'audio_map': '0:a?',
                'audio_codec': 'libopus',
                'audio_downmix': 'none',
                'video_codec': 'libx265',
                'preset': 'slow',
                'crf': '23',
                'pixel_format': 'yuv420p10le',
                'x265_params': 'level=5.1',
                'tune': 'animation',
                'output_format': 'matroska',
            },
        )
        assert job.audio_downmix == 'stereo'
        assert job.ffmpeg_params['audio_downmix'] == 'stereo'
        assert 'stereo downmix' in job.display_label()

    def test_resolve_fail_raises(self, tmp_path):
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        with pytest.raises(ValueError, match='copy'):
            Job(
                id='job-fallback',
                filename='in.mkv',
                original_path=str(src),
                video_id='hevc_rip',
                audio_id='copy',
                audio_downmix='stereo',
                ffmpeg_params=None,
            )


class TestBuildDownmixPlan:
    def test_mix_stereo_and_51(self):
        streams = [
            AudioStreamInfo(0, 2, 'stereo'),
            AudioStreamInfo(1, 6, '5.1'),
        ]
        plan = build_downmix_plan(streams, '0:a?')
        assert plan.filter_complex is not None
        assert ATSC_LORO_PAN in plan.filter_complex
        assert '[0:a:1]' in plan.filter_complex
        assert '0:a:0' not in plan.filter_complex
        assert plan.audio_maps == ['0:a:0', '[dm0]']
        assert any('skip' in line for line in plan.log_lines)
        assert any('downmix' in line for line in plan.log_lines)

    def test_two_stereo_no_pan(self):
        streams = [
            AudioStreamInfo(0, 2, 'stereo'),
            AudioStreamInfo(1, 2, 'stereo'),
        ]
        plan = build_downmix_plan(streams, '0:a?')
        assert plan.filter_complex is None
        assert plan.audio_maps == ['0:a:0', '0:a:1']

    def test_single_51_has_pan(self):
        streams = [AudioStreamInfo(0, 6, '5.1(side)')]
        plan = build_downmix_plan(streams, '0:a:0')
        assert plan.filter_complex is not None
        assert plan.audio_maps == ['[dm0]']


class TestProbeAudioStreams:
    def test_probe_parses_json(self):
        fake = SimpleNamespace(
            returncode=0,
            stdout='{"streams":[{"index":1,"channels":6,"channel_layout":"5.1"},'
                   '{"index":2,"channels":2,"channel_layout":"stereo"}]}',
            stderr='',
        )
        streams = probe_audio_streams('/tmp/x.mkv', 'ffprobe', run=lambda *a, **k: fake)
        assert len(streams) == 2
        assert streams[0].aindex == 0
        assert streams[0].channels == 6
        assert streams[1].channels == 2

    def test_probe_failure_raises(self):
        fake = SimpleNamespace(returncode=1, stdout='', stderr='boom')
        with pytest.raises(AudioDownmixError, match='ffprobe'):
            probe_audio_streams('/tmp/x.mkv', 'ffprobe', run=lambda *a, **k: fake)


class TestFfmpegWorkerDownmixCmd:
    def _worker(self):
        return FFmpegWorker(
            progress_callback=lambda *a: None,
            log_callback=lambda *a: None,
            status_callback=lambda *a: None,
        )

    def test_cmd_pan_only_on_51(self, monkeypatch, tmp_path):
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        out = tmp_path / 'out.mkv'

        streams = [
            AudioStreamInfo(0, 2, 'stereo'),
            AudioStreamInfo(1, 6, '5.1'),
        ]
        monkeypatch.setattr(
            'app.ffmpeg_worker.probe_audio_streams',
            lambda *a, **k: streams,
        )
        monkeypatch.setattr('app.ffmpeg_worker.get_ffmpeg_bin', lambda: 'ffmpeg')
        monkeypatch.setattr('app.ffmpeg_worker.get_ffprobe_bin', lambda: 'ffprobe')

        job = Job(
            id='job-1',
            filename='in.mkv',
            original_path=str(src),
            output_path=str(out),
            video_id='hevc_rip',
            audio_id='opus',
            audio_downmix='stereo',
            ffmpeg_params={
                'video_map': '0:v:0',
                'audio_map': '0:a?',
                'subtitle_map': None,
                'data_map': None,
                'video_codec': 'libx265',
                'subtitle_codec': None,
                'data_codec': None,
                'preset': 'slow',
                'crf': '23',
                'pixel_format': 'yuv420p10le',
                'x265_params': 'level=5.1:ref=6',
                'tune': 'animation',
                'audio_codec': 'libopus',
                'audio_bitrate': '160k',
                'audio_sample_rate': '48000',
                'audio_vbr': 'true',
                'output_format': 'matroska',
                'audio_downmix': 'stereo',
                'cpu_threads': '0',
                'thread_queue_size': '0',
                'x265_pools': '0',
                'update_metadata': 'false',
            },
        )
        worker = self._worker()
        # stop background worker loop from racing
        worker.stop_event.set()
        cmd = worker._build_ffmpeg_command(job, str(out))
        assert '-filter_complex' in cmd
        fc = cmd[cmd.index('-filter_complex') + 1]
        assert '[0:a:1]' in fc
        assert '0:a:0' not in fc
        assert '0:a:0' in cmd
        assert '[dm0]' in cmd
        assert cmd.count('-c:a') == 1

    def test_probe_fail_raises(self, monkeypatch, tmp_path):
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        out = tmp_path / 'out.mkv'
        monkeypatch.setattr(
            'app.ffmpeg_worker.probe_audio_streams',
            MagicMock(side_effect=AudioDownmixError('probe failed')),
        )
        monkeypatch.setattr('app.ffmpeg_worker.get_ffmpeg_bin', lambda: 'ffmpeg')
        job = Job(
            id='job-2',
            filename='in.mkv',
            original_path=str(src),
            output_path=str(out),
            video_id='hevc_rip',
            audio_id='opus',
            audio_downmix='stereo',
            ffmpeg_params={
                'video_map': '0:v:0',
                'audio_map': '0:a?',
                'subtitle_map': None,
                'data_map': None,
                'video_codec': 'libx265',
                'subtitle_codec': None,
                'data_codec': None,
                'preset': 'slow',
                'crf': '23',
                'pixel_format': 'yuv420p10le',
                'x265_params': 'level=5.1',
                'tune': 'animation',
                'audio_codec': 'libopus',
                'output_format': 'matroska',
                'audio_downmix': 'stereo',
                'cpu_threads': '0',
                'thread_queue_size': '0',
                'x265_pools': '0',
                'update_metadata': 'false',
            },
        )
        worker = self._worker()
        worker.stop_event.set()
        with pytest.raises(ValueError, match='Stereo downmix'):
            worker._build_ffmpeg_command(job, str(out))
