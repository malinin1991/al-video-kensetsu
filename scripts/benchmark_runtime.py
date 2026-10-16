#!/usr/bin/env python3
"""Сравнение runtime на API приложения и SHA256 без реальных медиапроцессов.

Запускать отдельно каждым интерпретатором: python scripts/benchmark_runtime.py
--output /tmp/python315.json. Данные и SQLite всегда во временном каталоге.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import sysconfig
from tempfile import TemporaryDirectory
import time
import warnings


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def legacy_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4096), b''):
            digest.update(block)
    return digest.hexdigest()


def measure_api(app, endpoint, *, requests, workers, repeats):
    def batch(count):
        latencies = []
        with app.test_client() as client:
            for _ in range(count):
                started = time.perf_counter()
                response = client.get(endpoint)
                assert response.status_code == 200, response.status_code
                assert response.data
                response.close()
                latencies.append((time.perf_counter() - started) * 1000)
        return latencies

    batch(5)  # прогрев маршрута и сериализации
    rounds = []
    for _ in range(repeats):
        counts = [requests // workers + (index < requests % workers) for index in range(workers)]
        started = time.perf_counter()
        cpu_started = time.process_time()
        if workers == 1:
            latencies = batch(requests)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                latencies = [latency for result in pool.map(batch, counts) for latency in result]
        elapsed = time.perf_counter() - started
        latencies.sort()
        rounds.append({
            'requests_per_second': requests / elapsed,
            'p95_ms': latencies[int((len(latencies) - 1) * 0.95)],
            'elapsed_seconds': elapsed,
            'cpu_seconds': time.process_time() - cpu_started,
        })
    return {
        'endpoint': endpoint, 'workers': workers, 'requests_per_round': requests,
        'requests_per_second': statistics.median(r['requests_per_second'] for r in rounds),
        'p95_ms': statistics.median(r['p95_ms'] for r in rounds), 'rounds': rounds,
    }


def run(args, directory):
    root = Path(directory)
    for key, suffix in (
        ('JOB_STORE_PATH', 'jobs.sqlite3'), ('APP_SETTINGS_PATH', 'settings.json'),
        ('UPLOADS_DIR', 'uploads'), ('OUTPUTS_DIR', 'outputs'), ('LOG_DIR', 'logs'),
    ):
        os.environ[key] = str(root / suffix)
    os.environ.update(LOG_LEVEL='CRITICAL', CONSOLE_LOG_LEVEL='CRITICAL', SOCKETIO_ASYNC_MODE='threading')
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as import_warnings, redirect_stdout(io.StringIO()):
        warnings.simplefilter('always')
        import psutil
        import yaml
        from app.main import app, ffmpeg_worker, socketio
        from app.models.job import Job, JobStatus
        from app.models.presets import _SAFE_YAML_LOADER
        from app.file_utils import calculate_file_hash
        from app.job_store import get_job_store
        import app.routes as routes
    import_seconds = time.perf_counter() - started
    ffmpeg_worker.stop_worker()
    for name in ('socketio.server', 'engineio.server'):
        logging.getLogger(name).disabled = True
    app.config.update(TESTING=True)
    routes.jobs_storage = {
        f'bench-{index:08d}': Job(
            id=f'bench-{index:08d}', filename=f'Серия [{index:04d}].mkv',
            original_path=str(root / 'unused.mkv'), status=JobStatus.DONE,
        ) for index in range(args.jobs)
    }
    store = get_job_store()
    body = {'job': next(iter(routes.jobs_storage.values())).to_dict(), 'created': True}
    store.claim_request('benchmark', 'benchmark')
    store.finish_request('benchmark', body, 200)

    from importlib.metadata import version
    result = {
        'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
        'machine': platform.machine(), 'cpu_count': os.cpu_count(),
        'cpu_quota': (Path('/sys/fs/cgroup/cpu.max').read_text().strip()
                      if Path('/sys/fs/cgroup/cpu.max').exists() else None),
        'configure_args': sysconfig.get_config_var('CONFIG_ARGS'),
        'free_threaded_build': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
        'gil_enabled_after_imports': sys._is_gil_enabled(),
        'jit_available': sys._jit.is_available(), 'jit_enabled': sys._jit.is_enabled(),
        'yaml_c_extension': yaml.__with_libyaml__,
        'import_warnings': [str(w.message) for w in import_warnings],
        'dependencies': {name: version(name) for name in (
            'Flask', 'Flask-SocketIO', 'Flask-Cors', 'python-socketio',
            'python-engineio', 'Werkzeug', 'psutil', 'PyYAML',
        )},
        'import_seconds': import_seconds, 'jobs': args.jobs, 'repeats': args.repeats,
        'api': [],
    }
    for endpoint in ('/api/jobs', '/api/internal/encode/requests/benchmark'):
        for workers in args.workers:
            result['api'].append(measure_api(
                app, endpoint, requests=args.requests, workers=workers, repeats=args.repeats,
            ))

    path = root / 'hash-fixture.mkv'
    with path.open('wb') as handle:
        block = bytes(range(256)) * 4096
        for _ in range(args.hash_mib):
            handle.write(block)
    methods = {'legacy_4k': legacy_hash, 'application': calculate_file_hash}
    timings = {name: [] for name in methods}
    expected = legacy_hash(path)  # прогрев файлового кэша; это не замер холодного диска
    for repeat in range(args.repeats):
        order = list(methods) if repeat % 2 == 0 else list(reversed(methods))
        for name in order:
            started = time.perf_counter()
            assert methods[name](path) == expected
            timings[name].append(time.perf_counter() - started)
    result['hash'] = {
        'size_mib': args.hash_mib, 'file_cache': 'warm',
        'seconds': {name: statistics.median(values) for name, values in timings.items()},
        'rounds': timings,
    }
    source = (Path(__file__).resolve().parents[1] / 'presets/video/hevc_auto_level_rip.yaml').read_text(encoding='utf-8')
    expected_yaml = yaml.load(source, Loader=yaml.SafeLoader)
    yaml_timings = {'safe_python': [], 'application_safe_loader': []}
    loaders = {'safe_python': yaml.SafeLoader, 'application_safe_loader': _SAFE_YAML_LOADER}
    for repeat in range(args.repeats):
        order = list(loaders) if repeat % 2 == 0 else list(reversed(loaders))
        for name in order:
            started = time.perf_counter()
            for _ in range(500):
                parsed = yaml.load(source, Loader=loaders[name])
            assert parsed == expected_yaml
            yaml_timings[name].append(time.perf_counter() - started)
    result['yaml_parse'] = {
        'documents_per_round': 500, 'fixture': 'hevc_auto_level_rip.yaml',
        'seconds': {name: statistics.median(values) for name, values in yaml_timings.items()},
        'rounds': yaml_timings,
    }
    result['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024 * 1024 if sys.platform == 'darwin' else 1024
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=200)
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--hash-mib', type=int, default=64)
    args = parser.parse_args()
    if min(args.jobs, args.requests, args.repeats, args.hash_mib, *args.workers) < 1:
        parser.error('Количество заданий, запросов, повторов, потоков и MiB должно быть положительным')
    with TemporaryDirectory(prefix='encoder-runtime-bench-') as directory:
        result = run(args, directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'output': str(args.output), 'gil_enabled': result['gil_enabled_after_imports'],
        'free_threaded_build': result['free_threaded_build'],
        'api_rps': [round(item['requests_per_second'], 1) for item in result['api']],
        'peak_rss_mib': round(result['peak_rss_mib'], 1),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
