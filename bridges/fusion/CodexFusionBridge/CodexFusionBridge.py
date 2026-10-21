"""Fusion main-thread bridge using the official custom-event API."""
import adsk.core
import adsk.fusion
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid

_configuration = json.loads(Path(__file__).with_name("bridge-config.json").read_text(encoding="utf-8-sig"))
WORKSPACE = Path(_configuration["workspace"]).resolve()
QUEUE = Path(_configuration.get("fusion_runtime", WORKSPACE / "work" / "fusion-runtime"))
EVENT_ID = "EngineeringMCPFusionBridge.v1.local"
_app = None
_handler = None
_event = None
_stop = threading.Event()
_worker = None
_pending = threading.Event()
_busy = False
_last_status = {}


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)


def heartbeat(state="ready", job_id=None):
    global _last_status
    _last_status = {
        "state": state, "timestamp": time.time(), "pid": os.getpid(),
        "version": _app.version, "workspace": str(WORKSPACE), "active_job": job_id,
        "active_document": _app.activeDocument.name if _app.activeDocument else None,
    }
    write_json(QUEUE / "status.json", _last_status)


class JobHandler(adsk.core.CustomEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        global _busy
        if _busy:
            return
        _busy = True
        try:
            heartbeat()
            for request in sorted((QUEUE / "requests").glob("*.json"))[:1]:
                job_id = request.stem
                reply = QUEUE / "responses" / (job_id + ".json")
                if reply.exists():
                    request.unlink()
                    continue
                output = io.StringIO()
                try:
                    job = json.loads(request.read_text(encoding="utf-8"))
                    if time.time() > job.get("expires_at", job.get("created_at", 0) + 120):
                        raise TimeoutError("Expired queued request; it was not executed")
                    if job["kind"] == "ping":
                        result = {"version": _app.version, "pid": os.getpid()}
                    elif job["kind"] == "run_script":
                        heartbeat("busy", job_id)
                        script = Path(job["script"]).resolve(strict=True)
                        script.relative_to(WORKSPACE.resolve())
                        namespace = {"__name__": "__codex_fusion_job__", "__file__": str(script)}
                        with contextlib.redirect_stdout(output):
                            exec(compile(script.read_text(encoding="utf-8-sig"), str(script), "exec"), namespace)
                            result = namespace["run"]({"workspace": str(WORKSPACE), "job_id": job_id})
                    else:
                        raise ValueError("Unknown job kind")
                    response = {"ok": True, "id": job_id, "result": result, "stdout": output.getvalue()}
                except Exception:
                    response = {"ok": False, "id": job_id, "error": traceback.format_exc(), "stdout": output.getvalue()}
                write_json(reply, response)
                request.unlink()
            heartbeat()
        except Exception:
            write_json(QUEUE / "bridge-error.json", {"error": traceback.format_exc(), "timestamp": time.time()})
        finally:
            _busy = False
            _pending.clear()


def poll():
    while not _stop.wait(1):
        if _busy and _last_status:
            write_json(QUEUE / "status.json", dict(_last_status, timestamp=time.time()))
            continue
        if not _pending.is_set():
            _pending.set()
            try:
                _app.fireCustomEvent(EVENT_ID)
            except Exception:
                _pending.clear()


def run(context):
    global _app, _handler, _event, _worker
    _app = adsk.core.Application.get()
    QUEUE.mkdir(parents=True, exist_ok=True)
    (QUEUE / "requests").mkdir(exist_ok=True)
    (QUEUE / "responses").mkdir(exist_ok=True)
    _stop.clear()
    _pending.clear()
    _event = _app.registerCustomEvent(EVENT_ID)
    _handler = JobHandler()
    _event.add(_handler)
    heartbeat()
    _worker = threading.Thread(target=poll, name="CodexFusionBridge", daemon=True)
    _worker.start()


def stop(context):
    _stop.set()
    if _worker:
        _worker.join(timeout=2)
    if _event and _handler:
        _event.remove(_handler)
    if _app:
        _app.unregisterCustomEvent(EVENT_ID)
        heartbeat("stopped")
