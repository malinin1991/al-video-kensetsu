"""Опциональный smoke: настоящий FFmpeg → MKVToolNix → скачивание через API.

RUN_REAL_MEDIA_TESTS=1 python -m pytest -q tests/test_real_media.py
Нужны FFmpeg/ffprobe/mkvmerge/mkvpropedit, без mock медиапроцессов.
"""

import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from app.ffmpeg_bootstrap import get_ffmpeg_bin, get_ffprobe_bin
from app.main import app, ffmpeg_worker
from app.mkvmerge_remux import get_mkvmerge_bin
import app.routes as routes


pytestmark = pytest.mark.skipif(
    os.getenv('RUN_REAL_MEDIA_TESTS') != '1',
    reason='Для настоящих медиапроцессов задайте RUN_REAL_MEDIA_TESTS=1',
)


@pytest.mark.parametrize('audio_id,downmix,codec,channels', [
    ('copy', 'none', 'aac', 6),
    ('opus', 'stereo', 'opus', 2),
])
def test_real_default_encode_metadata_download_and_receipt(
    tmp_path, monkeypatch, audio_id, downmix, codec, channels,
):
    library = tmp_path / 'library'
    library.mkdir()
    source = library / 'Серия [01].mkv'
    uploads = tmp_path / 'uploads'
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(library))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: ffmpeg_worker)

    subprocess.run([
        get_ffmpeg_bin(), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc2=size=256x144:rate=24:duration=1',
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=5.1:sample_rate=48000',
        '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        '-metadata:s:v:0', 'title=Original', str(source),
    ], check=True, capture_output=True, timeout=30)
    original_bytes = source.read_bytes()
    request_id = f'real-media-{uuid.uuid4().hex}'
    payload = {
        'path': str(source), 'request_id': request_id,
        'audio_id': audio_id, 'audio_downmix': downmix,
        'update_metadata': True, 'metadata_nickname': 'Review',
        'cpu_threads': 2, 'x265_pools': 1,
    }
    job = None
    try:
        with app.test_client() as client:
            submitted = client.post('/api/internal/encode', json=payload)
            assert submitted.status_code == 200, submitted.get_json()
            receipt = submitted.get_json()
            job = routes.get_job(receipt['job']['id'])
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                state = client.get(f'/api/jobs/{job.id}').get_json()
                if state['status'] in ('done', 'error', 'cancelled'):
                    break
                time.sleep(0.05)
            assert state['status'] == 'done', state.get('error_message') or state
            assert state['video_id'] == 'hevc_auto_level_rip'
            assert state['ffmpeg_params']['x265_params'] is None
            assert state['output_available'] is True
            assert state['progress'] == 100
            downloaded = client.get(state['download_url'])
            assert downloaded.status_code == 200
            assert len(downloaded.data) == state['output_size_bytes'] > 0
            downloaded.close()

            probe = json.loads(subprocess.run([
                get_ffprobe_bin(), '-v', 'error', '-show_streams', '-of', 'json',
                job.output_path,
            ], check=True, capture_output=True, text=True, timeout=30).stdout)
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
            assert video['codec_name'] == 'hevc'
            assert video['pix_fmt'] == 'yuv420p10le'
            assert audio['codec_name'] == codec
            assert audio['channels'] == channels
            identified = json.loads(subprocess.run([
                get_mkvmerge_bin(), '-J', job.output_path,
            ], check=True, capture_output=True, text=True, timeout=30).stdout)
            properties = next(t['properties'] for t in identified['tracks'] if t['type'] == 'video')
            assert properties['track_name'] == 'Original [Review]'
            assert properties['default_track'] is True
            assert properties['forced_track'] is True

            # Готовая квитанция остаётся снимком приёма, повтор не создаёт encode.
            replay = client.post('/api/internal/encode', json=payload)
            assert replay.status_code == 200
            assert replay.get_json() == receipt
            assert client.get(f'/api/internal/encode/requests/{request_id}').get_json() == receipt
            assert source.read_bytes() == original_bytes
            # После финализации в отдельном outputs остаётся только итоговый MKV.
            assert set(Path(job.output_path).parent.iterdir()) == {Path(job.output_path)}
    finally:
        if job is not None:
            ffmpeg_worker.stop_job(job.id)
            thread = ffmpeg_worker._job_threads.get(job.id)
            if thread:
                thread.join(timeout=10)
            routes.jobs_storage.pop(job.id, None)


def test_real_user_preset_keeps_parameters_when_edited_during_encode(tmp_path, monkeypatch):
    from app.models.presets import preset_manager
    source_dir = tmp_path / 'library'
    source_dir.mkdir()
    source = source_dir / 'User preset.mkv'
    monkeypatch.setenv('UPLOADS_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(source_dir))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: ffmpeg_worker)
    subprocess.run([
        get_ffmpeg_bin(), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=12:duration=1',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-an', str(source),
    ], check=True, capture_output=True, timeout=30)
    job = None
    try:
        with app.test_client() as client:
            definition = {'name': 'Мой быстрый HEVC', 'video_codec': 'libx265',
                          'output_format': 'matroska', 'preset': 'ultrafast', 'crf': '32',
                          'pixel_format': 'yuv420p10le'}
            created = client.post('/api/preset-editor/video', json={'definition': definition, 'set_default': True},
                                  headers={'If-Match': f'"{preset_manager.catalog_revision}"'})
            assert created.status_code == 201, created.json
            preset_id = created.json['id']
            response = client.post('/api/internal/encode', json={
                'path': str(source), 'update_metadata': False, 'cpu_threads': 1, 'x265_pools': 1,
            })
            assert response.status_code == 200, response.json
            job = routes.get_job(response.json['job']['id'])
            definition.update(name='Изменённый HEVC', crf='20')
            changed = client.put(f'/api/preset-editor/video/{preset_id}', json={'definition': definition},
                                 headers={'If-Match': f'"{preset_manager.catalog_revision}"'})
            assert changed.status_code == 200, changed.json
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and job.status.value in ('planned', 'in_progress'):
                time.sleep(0.05)
            assert job.status.value == 'done', job.error_message
            assert job.video_id == preset_id
            assert job.ffmpeg_params['crf'] == '32'
            assert job.display_label() == 'Мой быстрый HEVC / Copy'
            argv = ffmpeg_worker._build_ffmpeg_command(job)
            assert argv[argv.index('-crf') + 1] == '32'
            assert preset_manager.get_video(preset_id).crf == '20'
            probe = json.loads(subprocess.run([
                get_ffprobe_bin(), '-v', 'error', '-show_streams', '-of', 'json', job.output_path,
            ], check=True, capture_output=True, text=True, timeout=30).stdout)
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            assert video['codec_name'] == 'hevc'
            assert video['pix_fmt'] == 'yuv420p10le'
            assert client.get(f'/api/jobs/{job.id}/download').status_code == 200
    finally:
        if job is not None:
            ffmpeg_worker.stop_job(job.id)
            thread = ffmpeg_worker._job_threads.get(job.id)
            if thread:
                thread.join(timeout=10)
            routes.jobs_storage.pop(job.id, None)
