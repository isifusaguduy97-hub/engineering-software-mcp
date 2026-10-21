from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
import uuid

def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)

class Jobs:
    def __init__(self, config):
        self.config = config
        self.root = config.workspace / 'outputs' / 'mcp-runs'
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='engineering')
        self._locks = {name: threading.Lock() for name in ('comsol', 'fusion', 'labview')}

    def directory(self, ident):
        if not re.fullmatch(r'[0-9a-f]{32}', ident): raise ValueError('Invalid job id')
        path = (self.root / ident).resolve()
        path.relative_to(self.root.resolve())
        if not path.is_dir(): raise FileNotFoundError('Unknown job id')
        return path

    def submit(self, software, operation, parameters, function):
        if not self._locks[software].acquire(blocking=False):
            raise RuntimeError(f'{software} is already executing a task in this server. Check its job first.')
        ident = uuid.uuid4().hex
        folder = self.root / ident
        try:
            folder.mkdir(exist_ok=False)
            state = {'job_id': ident, 'software': software, 'operation': operation,
                     'state': 'queued', 'created_at': time.time(), 'owner_pid': os.getpid(),
                     'parameters': parameters, 'output_directory': str(folder), 'revision': 0}
            atomic_json(folder / 'job.json', state)
            self.pool.submit(self._execute, folder, state, function)
        except BaseException:
            self._locks[software].release()
            raise
        return {'job_id': ident, 'state': 'queued', 'output_directory': str(folder),
                'next': 'Call engineering_job_status with this job_id until completed or failed. Do not resubmit.'}

    def _write_state(self, folder, state):
        # Keep job.json as the immutable initial index. Windows scanners, readers
        # and some sandbox policies can deny replacing an existing file from a
        # worker thread. New, uniquely named snapshots avoid that operation.
        state['revision'] = state.get('revision', 0) + 1
        snapshot = folder / 'states' / f"{state['revision']:06d}-{uuid.uuid4().hex}.json"
        atomic_json(snapshot, state)

    def _execute(self, folder, state, function):
        lockfile = None
        acquired = False
        try:
            lockdir = self.config.workspace / 'work' / 'engineering-locks'
            lockdir.mkdir(parents=True, exist_ok=True)
            lockfile = (lockdir / (state['software'] + '.lock')).open('a+b')
            lockfile.seek(0); lockfile.write(b'0'); lockfile.flush(); lockfile.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            else:
                import fcntl
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            state.update(state='running', started_at=time.time())
            self._write_state(folder, state)
            result = function(folder)
            # Serialization failures are failed jobs, not lost background-thread
            # exceptions which would leave a running job and its lock behind.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            state.update(state='completed', result=result)
        except Exception as exc:
            state.update(state='failed', error=str(exc), error_type=type(exc).__name__)
            (folder / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
        finally:
            try:
                state['finished_at'] = time.time()
                self._write_state(folder, state)
            finally:
                try:
                    if lockfile:
                        try:
                            if acquired and os.name == 'nt':
                                import msvcrt
                                lockfile.seek(0); msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
                        finally:
                            lockfile.close()
                finally:
                    self._locks[state['software']].release()

    def status(self, ident):
        folder = self.directory(ident)
        state = None
        # A malformed/interrupted snapshot must not hide the last valid state.
        for snapshot in sorted((folder / 'states').glob('*.json'), reverse=True):
            try:
                candidate = json.loads(snapshot.read_text(encoding='utf-8'))
                if not isinstance(candidate, dict) or candidate.get('job_id') != ident:
                    continue
                state = candidate
                break
            except (OSError, ValueError):
                continue
        if state is None:
            state = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
        if state['state'] in ('queued', 'running'):
            state['elapsed_seconds'] = round(time.time() - state.get('started_at', state['created_at']), 1)
            if state['owner_pid'] != os.getpid():
                state['note'] = 'Task belongs to another or previous server process. Inspect logs and application state before any retry.'
            if state['elapsed_seconds'] > self.config.max_job_seconds:
                state['overdue'] = True
                state['note'] = 'Time budget exceeded; execution may still be active. This is not cancellation. Inspect logs before retrying.'
        return state

    def artifacts(self, ident):
        root = self.directory(ident)
        return [{'name': p.relative_to(root).as_posix(), 'bytes': p.stat().st_size, 'path': str(p)}
                for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink() and p.suffix not in ('.tmp', '.lock')]

    def read_text(self, ident, name, max_chars=12000):
        root = self.directory(ident)
        path = (root / name).resolve(strict=True)
        path.relative_to(root)
        if path.suffix.lower() not in ('.json', '.log', '.csv', '.txt', '.java', '.py', '.svg'):
            raise ValueError('Only text logs/data/source in this job can be read. Use file paths for binary models.')
        if not 1 <= max_chars <= 50000: raise ValueError('max_chars must be 1..50000')
        with path.open(encoding='utf-8-sig', errors='replace') as file:
            value = file.read(max_chars + 1)
        return {'text': value[:max_chars], 'truncated': len(value) > max_chars, 'path': str(path)}
