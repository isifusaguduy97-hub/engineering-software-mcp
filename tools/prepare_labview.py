"""Generate typed wrappers from the locally installed LabVIEW type library.

Does not start LabVIEW, register COM components, or modify NI installation files.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sys
import uuid

from _common import DEFAULT_CONFIG, PROJECT, absolute_path, atomic_write, read_config, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--labview-root", help="Override configured LabVIEW root for wrapper generation")
    args = parser.parse_args(argv)
    config = read_config(absolute_path(args.config))
    root_value = args.labview_root or config.get("labview_root")
    if not root_value:
        parser.error("LabVIEW is not configured. Re-run tools/setup.py --labview-root PATH.")
    root = absolute_path(root_value)
    tlb = root / "resource" / "labview.tlb"
    if not tlb.is_file():
        parser.error(f"LabVIEW type library not found: {tlb}")
    try:
        import pythoncom
        import win32com
        import win32com.gen_py
    except ImportError as exc:
        raise ValueError("pywin32 is required. Run .venv\\Scripts\\python.exe -m pip install pywin32==312") from exc
    cache = PROJECT / "work" / "labview-generated"
    cache.mkdir(parents=True, exist_ok=True)
    win32com.__gen_path__ = str(cache)
    win32com.gen_py.__path__ = [str(cache)]
    from win32com.client import gencache, makepy
    gencache.is_readonly = False
    destination = PROJECT / "bridges" / "labview" / "labview_bindings.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache / f"labview_bindings-{uuid.uuid4().hex}.py"
    try:
        library = pythoncom.LoadTypeLib(str(tlb))
        attributes = library.GetLibAttr()
        # file= bypasses global generated-module registration and cache writes.
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            makepy.GenerateFromTypeLibSpec(library, file=handle, bForDemand=False, verboseLevel=0)
        text = temporary.read_text(encoding="utf-8")
        text = re.sub(r"coding[:=]\s*mbcs", "coding: utf-8", text, count=1)
        tree = ast.parse(text)
        if "_Application" not in {node.name for node in tree.body if isinstance(node, ast.ClassDef)}:
            raise ValueError("The selected type library does not define the required typed _Application wrapper.")
        if destination.exists() and destination.read_text(encoding="utf-8-sig") != text:
            backup_dir = PROJECT / "work" / "labview-bindings-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            backup = backup_dir / f"labview_bindings-{suffix}.py"
            backup.write_bytes(destination.read_bytes())
            print(f"Previous generated wrapper preserved: {backup}")
        atomic_write(destination, text)
        write_json(cache / "typelib-source.json", {
            "path": str(tlb), "guid": str(attributes[0]), "lcid": attributes[1],
            "major": attributes[3], "minor": attributes[4],
            "generated_file": str(destination),
        })
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Generated typed LabVIEW wrappers: {destination}")
    print("Next, double-click Start_LabVIEW.cmd in your normal desktop session and keep its window open.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"LabVIEW preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
