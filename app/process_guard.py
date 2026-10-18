"""Отдельный guardian: смерть сервера закрывает pipe и завершает всё дерево tool.

Файл запускается напрямую без импорта приложения и его побочных эффектов.
"""

import os
import signal
import subprocess
import sys
import threading
import time


def main():
    lifeline, lease, completion = map(int, sys.argv[1:4])
    stopped = threading.Event()

    def stop(*_):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def monitor():
        while os.read(lifeline, 1):
            pass
        stopped.set()

    threading.Thread(target=monitor, daemon=True).start()
    child = subprocess.Popen(sys.argv[4:], stdin=subprocess.DEVNULL,
                             start_new_session=True,
                             pass_fds=(lease,) + ((completion,) if completion >= 0 else ()))
    # WNOWAIT удерживает PID лидера до последнего killpg: номер группы не может
    # перейти чужому процессу между reap и сигналом оставшимся потомкам.
    def exited():
        return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    while not exited():
        if stopped.wait(0.05):
            try:
                os.killpg(child.pid, signal.SIGTERM)
                deadline = time.monotonic()+2
                while not exited() and time.monotonic() < deadline:
                    time.sleep(0.02)
            except ProcessLookupError:
                pass
            break
    # Завершить оставшихся потомков даже если основной tool уже вышел.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()
    return child.returncode if child.returncode >= 0 else 128-child.returncode


if __name__ == '__main__':
    sys.exit(main())
