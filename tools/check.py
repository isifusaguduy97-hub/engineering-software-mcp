"""Read-only local setup diagnostics; does not activate software or submit jobs."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
import time

from _common import DEFAULT_CONFIG, PROJECT, absolute_path, read_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    data = read_config(absolute_path(args.config))
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Project: {PROJECT}")
    print(f"Workspace: {data['workspace']}")
    try:
        print(f"MCP SDK: {importlib.metadata.version('mcp')}")
    except importlib.metadata.PackageNotFoundError:
        print("MCP SDK: MISSING. Run Setup.cmd or install requirements.txt in .venv.")
    root = data.get("comsol_root")
    batch = Path(root) / "bin" / "win64" / "comsolbatch.exe" if root else None
    print(f"COMSOL executable: {batch if batch and batch.is_file() else 'not configured/found'}")
    root = data.get("labview_root")
    tlb = Path(root) / "resource" / "labview.tlb" if root else None
    print(f"LabVIEW type library: {tlb if tlb and tlb.is_file() else 'not configured/found'}")
    print(f"LabVIEW typed bindings: {(PROJECT / 'bridges' / 'labview' / 'labview_bindings.py').is_file()}")
    print(f"pywin32 installed: {importlib.util.find_spec('win32com') is not None}")
    for name in ("fusion", "labview"):
        runtime = absolute_path(data.get(f"{name}_runtime") or Path(data["workspace"]) / "work" / f"{name}-runtime")
        status_path = runtime / "status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8-sig"))
            age = time.time() - float(status["timestamp"])
            fresh = status.get("state") == "ready" and -5 <= age <= 10
            print(f"{name} heartbeat: state={status.get('state')}, age={age:.1f}s, fresh={fresh}")
        except (OSError, ValueError, KeyError, TypeError):
            print(f"{name} heartbeat: unavailable. Start the corresponding desktop connector when needed.")
    print(f"Arbitrary modeling scripts enabled: {data.get('enable_scripts', False)}")
    print("These are setup diagnostics only. A fresh heartbeat plus a successful MCP ping is required to confirm a live connection.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Configuration check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
