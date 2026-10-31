"""Nonblocking per-task OS lock, automatically released on process exit."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from .catalog import ToolError


@contextmanager
def task_lock(database, task_id):
    folder = Path(str(Path(database).resolve()) + '.locks')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(task_id.encode()).hexdigest() + '.lock')
    handle = open(path, 'a+b')
    acquired = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ToolError('TASK_BUSY', 'Another process owns this task') from exc
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
