"""Владение сервером и lifeline медиапроцессов на POSIX (включая Docker)."""

import atexit
import os
import subprocess
import sys
import time
import select
import weakref
import threading

_lease = None
_lifeline = None


class MediaProcessStillRunning(RuntimeError):
    """Выход guardian ещё не подтверждает завершение всех его потомков."""

    def __init__(self, message, process):
        super().__init__(message)
        self.process = process


def acquire_execution(path, timeout=15):
    global _lease, _lifeline
    if os.name != 'posix':
        raise RuntimeError('Восстановление исполнения требует POSIX; на Windows используйте Docker')
    import fcntl
    path = os.path.realpath(os.path.abspath(path))
    lease = open(path + '.execution.lock', 'a+b')
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                lease.close()
                raise RuntimeError('База уже используется сервером или прежним медиапроцессом')
            time.sleep(0.1)
    _lease = lease
    _lifeline = os.pipe()
    atexit.register(release_execution)


def release_execution():
    global _lease, _lifeline
    if _lifeline is not None:
        for fd in _lifeline:
            os.close(fd)
        _lifeline = None
    if _lease is not None:
        _lease.close()
        _lease = None


def guarded_command(command, *, completion_fd=-1):
    """Lease наследуется при Popen: окна без владельца после создания процесса нет."""
    if _lease is None:
        return command, {}
    return [sys.executable, os.path.join(os.path.dirname(__file__), 'process_guard.py'),
            str(_lifeline[0]), str(_lease.fileno()), str(completion_fd), *command], {
                'pass_fds': (_lifeline[0], _lease.fileno()) + ((completion_fd,) if completion_fd >= 0 else ()),
            }


def popen_media(command, **kwargs):
    if _lease is None:
        return subprocess.Popen(command, **kwargs)
    reader, writer = os.pipe()
    try:
        command, options = guarded_command(command, completion_fd=writer)
        process = subprocess.Popen(command, **options, **kwargs)
    except BaseException:
        os.close(reader)
        raise
    finally:
        os.close(writer)
    process._completion_fd = reader
    process._completion_lock = threading.Lock()
    process._completion_finalizer = weakref.finalize(process, os.close, reader)
    return process


def media_process_finished(process, timeout=0):
    lock = getattr(process, '_completion_lock', None)
    if lock is not None:
        with lock:
            return _check_completion(process, timeout)
    return _check_completion(process, timeout)


def _check_completion(process, timeout):
    reader = getattr(process, '_completion_fd', None)
    if type(reader) is not int:
        if getattr(process, '_media_finished', False) is True:
            return True
        return process.poll() is not None
    if not select.select([reader], [], [], timeout)[0]:
        return False
    if os.read(reader, 1) != b'':
        return False
    process._completion_finalizer.detach()
    os.close(reader)
    process._completion_fd = None
    process._media_finished = True
    return True


def run_media(command, **kwargs):
    if _lease is None:
        return subprocess.run(command, **kwargs)
    timeout = kwargs.pop('timeout', None)
    check = kwargs.pop('check', False)
    if kwargs.pop('capture_output', False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with popen_media(command, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if not media_process_finished(process, timeout=5):
                raise MediaProcessStillRunning('Медиапроцесс остался жив после остановки guardian', process)
            raise
        if not media_process_finished(process, timeout=5):
            raise MediaProcessStillRunning('Guardian завершился, но медиапроцесс ещё жив', process)
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
