"""Submit a local Python modeling script to the running Fusion add-in."""
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

QUEUE = Path(os.environ.get("ENGINEERING_MCP_FUSION_RUNTIME", _settings.get("fusion_runtime", WORKSPACE / "work" / "fusion-runtime")))


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
    status_path = QUEUE / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {"state": "not_started"}
    if args.command == "status":
        status["fresh"] = time.time() - status.get("timestamp", 0) < 15
        print(json.dumps(status, ensure_ascii=False))
        return
    if status.get("state") != "ready" or time.time() - status.get("timestamp", 0) > 15:
        raise RuntimeError("Fusion bridge is not ready. Start Fusion and run the CodexFusionBridge add-in.")
    job_id = uuid.uuid4().hex
    job = {"kind": "ping", "created_at": time.time(), "expires_at": time.time() + args.timeout}
    if args.command == "run":
        script = args.script.resolve(strict=True)
        script.relative_to(WORKSPACE)
        job.update(kind="run_script", script=str(script))
    request = QUEUE / "requests" / (job_id + ".json")
    temporary = request.with_suffix(".tmp")
    temporary.write_text(json.dumps(job), encoding="utf-8")
    os.replace(temporary, request)
    response = QUEUE / "responses" / (job_id + ".json")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if response.exists():
            data = json.loads(response.read_text(encoding="utf-8"))
            print(json.dumps(data, ensure_ascii=False))
            return 0 if data["ok"] else 1
        time.sleep(0.25)
    # Remove a job that is still waiting, so a timed-out modeling request does
    # not unexpectedly execute later. A request already executing may finish.
    if request.exists():
        request.unlink()
    raise TimeoutError(f"Fusion did not finish job {job_id}; inspect {response} before retrying.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
