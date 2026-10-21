"""User-started local connector for LabVIEW ActiveX in a desktop session.

Run normally, without administrator privileges. This is a LabVIEW connector;
it must not be used as a general-purpose shell or to change OS security.
"""
from pathlib import Path
import contextlib
import io
import importlib.util
import json
import os
import site
import sys
import time
import traceback


def _bridge_settings():
    config_file = Path(os.environ.get("ENGINEERING_MCP_CONFIG", Path(__file__).resolve().parents[2] / "config.local.json"))
    return json.loads(config_file.read_text(encoding="utf-8-sig")) if config_file.is_file() else {}

_settings = _bridge_settings()
WORKSPACE = Path(os.environ.get("ENGINEERING_MCP_WORKSPACE", _settings.get("workspace", Path(__file__).resolve().parents[2]))).resolve()

RUNTIME = Path(os.environ.get("ENGINEERING_MCP_LABVIEW_RUNTIME", _settings.get("labview_runtime", WORKSPACE / "work" / "labview-runtime")))
RUNTIME.mkdir(parents=True, exist_ok=True)
site.addsitedir(str(RUNTIME / "python-deps"))


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)


def main():
    import pythoncom
    import win32com.client
    # Load the locally generated official LabVIEW typelib before creating COM
    # wrappers. Late-bound dispatch can misclassify VI methods as properties.
    bindings_path = Path(__file__).with_name("labview_bindings.py")
    spec = importlib.util.spec_from_file_location("codex_labview_bindings", bindings_path)
    bindings = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bindings
    spec.loader.exec_module(bindings)
    pythoncom.CoInitialize()
    (RUNTIME / "requests").mkdir(exist_ok=True)
    (RUNTIME / "responses").mkdir(exist_ok=True)
    # The file lock prevents two hosts from executing the same modeling job.
    import msvcrt
    lock = (RUNTIME / "host.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    write_json(RUNTIME / "status.json", {"state": "connecting", "timestamp": time.time(), "pid": os.getpid()})
    lv = bindings._Application(win32com.client.Dispatch("LabVIEW.Application"))
    print("LabVIEW connected:", lv.Version)
    print("Keep this window open while Codex uses LabVIEW. Ctrl+C stops the connector.")
    try:
        while True:
            write_json(RUNTIME / "status.json", {
                "state": "ready", "timestamp": time.time(), "pid": os.getpid(),
                "version": lv.Version, "name": lv.AppName, "directory": lv.ApplicationDirectory,
            })
            for request in sorted((RUNTIME / "requests").glob("*.json"))[:1]:
                job_id = request.stem
                response_path = RUNTIME / "responses" / (job_id + ".json")
                if response_path.exists():
                    request.unlink()
                    continue
                output = io.StringIO()
                try:
                    job = json.loads(request.read_text(encoding="utf-8"))
                    if time.time() > job.get("expires_at", job.get("created_at", 0) + 120):
                        raise TimeoutError("Expired queued request; it was not executed")
                    if job["kind"] == "ping":
                        result = {"version": lv.Version, "name": lv.AppName}
                    elif job["kind"] == "run_script":
                        write_json(RUNTIME / "status.json", {"state": "busy", "timestamp": time.time(), "pid": os.getpid(), "active_job": job_id, "version": lv.Version})
                        script = Path(job["script"]).resolve(strict=True)
                        script.relative_to(WORKSPACE)
                        namespace = {"__name__": "__codex_labview_job__", "__file__": str(script)}
                        with contextlib.redirect_stdout(output):
                            exec(compile(script.read_text(encoding="utf-8-sig"), str(script), "exec"), namespace)
                            result = namespace["run"]({"lv": lv, "workspace": str(WORKSPACE), "job_id": job_id})
                    else:
                        raise ValueError("Unknown job kind")
                    response = {"ok": True, "id": job_id, "result": result, "stdout": output.getvalue()}
                except Exception:
                    response = {"ok": False, "id": job_id, "error": traceback.format_exc(), "stdout": output.getvalue()}
                write_json(response_path, response)
                request.unlink()
            pythoncom.PumpWaitingMessages()
            time.sleep(0.5)
    finally:
        write_json(RUNTIME / "status.json", {"state": "stopped", "timestamp": time.time(), "pid": os.getpid()})
        lv = None
        pythoncom.CoUninitialize()
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        error = traceback.format_exc()
        write_json(RUNTIME / "status.json", {"state": "error", "timestamp": time.time(), "error": error})
        print(error)
        sys.exit(1)
