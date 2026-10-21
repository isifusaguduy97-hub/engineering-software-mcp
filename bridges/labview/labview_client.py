"""Submit a LabVIEW automation script to the user-started desktop connector."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


def _bridge_settings():
    config_file = Path(os.environ.get("ENGINEERING_MCP_CONFIG", Path(__file__).resolve().parents[2] / "config.local.json"))
    return json.loads(config_file.read_text(encoding="utf-8-sig")) if config_file.is_file() else {}

_settings = _bridge_settings()
WORKSPACE = Path(os.environ.get("ENGINEERING_MCP_WORKSPACE", _settings.get("workspace", Path(__file__).resolve().parents[2]))).resolve()

RUNTIME = Path(os.environ.get("ENGINEERING_MCP_LABVIEW_RUNTIME", _settings.get("labview_runtime", WORKSPACE / "work" / "labview-runtime")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    ping = sub.add_parser("ping")
    ping.add_argument("--timeout", type=float, default=30)
    run = sub.add_parser("run")
    run.add_argument("script", type=Path)
    run.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    status_file = RUNTIME / "status.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {"state": "not_started"}
    if args.command == "status":
        status["fresh"] = time.time() - status.get("timestamp", 0) < 15
        print(json.dumps(status, ensure_ascii=False))
        return
    if status.get("state") != "ready" or time.time() - status.get("timestamp", 0) > 15:
        raise RuntimeError("LabVIEW connector is not ready. Open Start_LabVIEW_Connector.cmd from the desktop file browser.")
    job_id = uuid.uuid4().hex
    job = {"kind": "ping", "created_at": time.time(), "expires_at": time.time() + args.timeout}
    if args.command == "run":
        script = args.script.resolve(strict=True)
        script.relative_to(WORKSPACE)
        job.update(kind="run_script", script=str(script))
    request = RUNTIME / "requests" / (job_id + ".json")
    temporary = request.with_suffix(".tmp")
    temporary.write_text(json.dumps(job), encoding="utf-8")
    os.replace(temporary, request)
    response_path = RUNTIME / "responses" / (job_id + ".json")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            print(json.dumps(response, ensure_ascii=False))
            return 0 if response["ok"] else 1
        time.sleep(0.25)
    if request.exists():
        request.unlink()
    raise TimeoutError(f"Job {job_id} timed out; inspect {response_path} before retrying.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
