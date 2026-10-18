#!/usr/bin/env python3
"""Настоящий Docker restart/kill/recreate smoke с отдельными временными данными.

python scripts/check_job_recovery_container.py --image IMAGE [--media-dir DIR]
Без --media-dir используется штатная подготовка FFmpeg образа.
"""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    result = subprocess.run(['docker', *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--media-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    name = 'kensetsu-recovery-'+uuid.uuid4().hex[:10]
    root = Path(tempfile.mkdtemp(prefix=name+'-'))
    for folder in ('uploads', 'outputs', 'settings', 'logs', 'library', 'ffmpeg'):
        (root/folder).mkdir()
    report = {'image': args.image, 'container': name, 'data_dir': str(root), 'checks': []}
    base = None

    def create():
        command = ['run', '-d', '--init', '--name', name, '--stop-timeout', '20', '-p', '127.0.0.1::5000',
                   '-e', 'INTERNAL_SOURCE_ROOTS=/fixtures', '-e', 'LOG_LEVEL=WARNING',
                   '-e', 'CONSOLE_LOG_LEVEL=WARNING']
        for folder in ('uploads', 'outputs', 'settings', 'logs', 'ffmpeg'):
            command += ['-v', f'{root/folder}:/app/{folder}']
        command += ['-v', f'{root/"library"}:/fixtures']
        if args.media_dir:
            command += ['-v', f'{args.media_dir.resolve()}:/media:ro',
                        '-e', 'FFMPEG_BIN=/media/ffmpeg', '-e', 'FFPROBE_BIN=/media/ffprobe']
        command.append(args.image)
        docker(*command)

    def request(path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base+path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def wait_ready():
        nonlocal base
        port = docker('port', name, '5000/tcp').split(':')[-1]
        base = 'http://127.0.0.1:'+port
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            try:
                if request('/health')['status'] == 'healthy':
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError('Сервер не готов: '+docker('logs', '--tail', '60', name))

    def state(job_id):
        return request('/api/jobs/'+job_id)

    def wait(predicate, timeout=180):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError('Превышено время ожидания: '+json.dumps(request('/api/jobs'), ensure_ascii=False))

    def submit(path='/fixtures/source.mkv', metadata=True):
        key = uuid.uuid4().hex
        receipt = request('/api/internal/encode', {
            'path': path, 'request_id': key, 'audio_id': 'copy',
            'cpu_threads': 1, 'x265_pools': 1, 'update_metadata': metadata,
            'metadata_nickname': 'Recovery QA',
        })
        return receipt['job']['id'], key, receipt

    try:
        create()
        wait_ready()
        report['image_id'] = docker('inspect', name, '--format', '{{.Image}}')
        report['python'] = docker('exec', name, 'python', '-c', 'import sys; print(sys.version.split()[0])')
        ffmpeg = '/media/ffmpeg' if args.media_dir else '/app/ffmpeg/bin/ffmpeg'
        ffprobe = '/media/ffprobe' if args.media_dir else '/app/ffmpeg/bin/ffprobe'
        report['ffmpeg'] = docker('exec', name, ffmpeg, '-version').splitlines()[0]
        docker('exec', name, ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=12',
               '-c:v', 'libx264', '-preset', 'ultrafast', '/fixtures/source.mkv')
        docker('exec', name, 'python', '-c',
               "from pathlib import Path; Path('/fixtures/bad.mkv').write_bytes(b'not a video')")
        error_id, _, _ = submit('/fixtures/bad.mkv')
        wait(lambda: state(error_id)['status'] == 'error')
        for mode, slots in [('restart', 1), ('kill', 1), ('recreate', 1), ('parallel', 2)]:
            request('/api/settings', {'max_concurrent_encodes': slots}, method='PUT')
            submissions = [submit(metadata=mode != 'kill') for _ in range(slots+2)]
            ids = [item[0] for item in submissions]
            cancel_id, _, _ = submit()
            request('/api/jobs/'+cancel_id+'/stop', {}, method='POST')
            wait(lambda: all(state(job_id)['status'] == 'in_progress' and state(job_id)['progress'] > 0
                             for job_id in ids[:slots]))
            before = [state(job_id) for job_id in ids]
            assert all(item['status'] == 'planned' for item in before[slots:]), before
            print(f'{mode}: до остановки {slots} кодируются, 2 ожидают', flush=True)
            if mode in ('restart', 'parallel'):
                docker('restart', '--time', '20', name)
            elif mode == 'kill':
                docker('kill', '--signal', 'KILL', name)
                docker('start', name)
            else:
                docker('rm', '-f', name)
                create()
            wait_ready()
            wait(lambda: all(state(job_id)['status'] in ('done', 'error') for job_id in ids))
            after = [state(job_id) for job_id in ids]
            assert all(job['status'] == 'done' for job in after), after
            assert [job['attempt_no'] for job in after] == [2]*slots+[1, 1], after
            for old, new in zip(before, after):
                for key in ('id', 'created_at', 'input_path', 'request_id', 'ffmpeg_params', 'preset_snapshot'):
                    assert old[key] == new[key], key
                assert new['output_available']
            assert state(error_id)['status'] == 'error' and state(error_id)['attempt_no'] == 1
            assert state(cancel_id)['status'] == 'cancelled' and state(cancel_id)['attempt_no'] == 1
            for job_id, key, receipt in submissions:
                assert request('/api/internal/encode/requests/'+key) == receipt
            records = json.loads(docker('exec', name, 'python', '-c',
                "import sqlite3,json; d=sqlite3.connect('/app/settings/jobs.sqlite3'); "
                "print(json.dumps([json.loads(r[0]) for r in d.execute('SELECT record FROM jobs')]))"))
            selected = {job['id']: job for job in records if job['id'] in ids}
            assert [selected[j]['dispatch_order'] for j in ids] == sorted(selected[j]['dispatch_order'] for j in ids)
            for job_id in ids:
                probe = json.loads(docker('exec', name, ffprobe, '-v', 'error', '-show_streams', '-of', 'json', selected[job_id]['output_path']))
                assert next(s for s in probe['streams'] if s['codec_type'] == 'video')['codec_name'] == 'hevc'
                assert not Path(selected[job_id]['original_path'].replace('/app/uploads', str(root/'uploads'))).exists()
            with urllib.request.urlopen(base+after[0]['download_url'], timeout=20) as response:
                assert len(response.read()) == after[0]['output_size_bytes'] > 0
            assert not list((root/'outputs').glob('*.partial.mkv'))
            report['checks'].append({'mode': mode, 'slots': slots, 'ids': ids,
                                     'attempts': [job['attempt_no'] for job in after], 'result': 'passed'})
            print(f'{mode}: восстановлены в нужном порядке, все MKV проверены', flush=True)
        terminal = {job['id']: (job['status'], job['attempt_no']) for job in request('/api/jobs')}
        docker('restart', '--time', '20', name)
        wait_ready()
        assert {job['id']: (job['status'], job['attempt_no']) for job in request('/api/jobs')} == terminal
        report['terminal_restart'] = 'passed'
        print('Все проверки контейнера пройдены; error/cancelled/done не повторяются', flush=True)
    except BaseException:
        try:
            report['logs'] = docker('logs', '--tail', '100', name)
        except subprocess.CalledProcessError:
            pass
        raise
    finally:
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


if __name__ == '__main__':
    main()
