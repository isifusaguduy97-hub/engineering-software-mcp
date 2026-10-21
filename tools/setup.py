"""Detect local engineering software and write this project's configuration.

This command neither activates COM nor changes Codex or system configuration.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib

from _common import DEFAULT_CONFIG, PROJECT, absolute_path, atomic_write, write_json


def version_key(path: Path) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", str(path)))


def detect_comsol() -> Path | None:
    candidates = []
    for key in ("COMSOL_ROOT", "COMSOL_HOME"):
        if os.environ.get(key):
            candidates.append(absolute_path(os.environ[key]))
    bases = [Path(os.environ.get("ProgramFiles", r"C:\Program Files"))]
    bases.extend(Path(f"{drive}:\\") for drive in "CDEFG")
    for base in bases:
        if base.is_dir():
            candidates.extend(sorted(base.glob("COMSOL*/Multiphysics"), key=version_key, reverse=True))
            candidates.extend(sorted(base.glob("COMSOL/COMSOL*/Multiphysics"), key=version_key, reverse=True))
    return next((candidate.resolve() for candidate in candidates
                 if (candidate / "bin" / "win64" / "comsolbatch.exe").is_file()), None)


def detect_labview() -> Path | None:
    candidates = []
    if os.environ.get("LABVIEW_ROOT"):
        candidates.append(absolute_path(os.environ["LABVIEW_ROOT"]))
    for key, fallback in (("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                          ("ProgramFiles", r"C:\Program Files")):
        base = Path(os.environ.get(key, fallback)) / "National Instruments"
        if base.is_dir():
            candidates.extend(sorted(base.glob("LabVIEW*"), key=version_key, reverse=True))
    return next((candidate.resolve() for candidate in candidates
                 if (candidate / "resource" / "labview.tlb").is_file()), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comsol-root", help="COMSOL Multiphysics folder, not the bin folder")
    parser.add_argument("--labview-root", help="LabVIEW folder containing resource/labview.tlb")
    parser.add_argument("--workspace", help="Folder in which engineering jobs and outputs may be created")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Configuration output (default: project/config.local.json)")
    parser.add_argument("--toml-output", default=str(PROJECT / "codex-mcp.toml"), help="Codex snippet output")
    args = parser.parse_args(argv)
    config_path = absolute_path(args.config)
    data = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("An existing configuration must be a JSON object.")
    previous_workspace = absolute_path(data["workspace"]) if data.get("workspace") else None
    workspace = absolute_path(args.workspace or data.get("workspace") or PROJECT)
    if args.comsol_root:
        comsol = absolute_path(args.comsol_root)
        if not (comsol / "bin" / "win64" / "comsolbatch.exe").is_file():
            parser.error(f"COMSOL batch executable not found below {comsol}")
    else:
        comsol = absolute_path(data["comsol_root"]) if data.get("comsol_root") else detect_comsol()
    if args.labview_root:
        labview = absolute_path(args.labview_root)
        if not (labview / "resource" / "labview.tlb").is_file():
            parser.error(f"LabVIEW type library not found below {labview}")
    else:
        labview = absolute_path(data["labview_root"]) if data.get("labview_root") else detect_labview()
    data.update(workspace=str(workspace), comsol_root=str(comsol) if comsol else None,
                labview_root=str(labview) if labview else None)
    data.setdefault("enable_scripts", False)
    if not isinstance(data["enable_scripts"], bool):
        parser.error("enable_scripts must be true or false.")
    # Keep custom queues, while moving default queues along with the workspace.
    for name in ("fusion", "labview"):
        key = f"{name}_runtime"
        old_default = previous_workspace / "work" / f"{name}-runtime" if previous_workspace else None
        if not data.get(key) or (old_default and absolute_path(data[key]) == old_default):
            data[key] = str(workspace / "work" / f"{name}-runtime")
    for queue_key in ("fusion_runtime", "labview_runtime"):
        data[queue_key] = str(absolute_path(data[queue_key]))
    for folder in (workspace / "work", workspace / "outputs"):
        folder.mkdir(parents=True, exist_ok=True)
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    python = PROJECT / ".venv" / "Scripts" / "python.exe"
    snippet = (
        "# Generated local snippet. Add this section in your MCP client settings.\n"
        "# This file is NOT added to Codex settings automatically.\n"
        "[mcp_servers.engineering-software]\n"
        f"command = {quote(python)}\n"
        "args = [\"-m\", \"engineering_mcp.server\", \"--config\", " + quote(config_path) + "]\n"
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 600\n"
        "\n[mcp_servers.engineering-software.env]\n"
        f"PYTHONPATH = {quote(PROJECT)}\n"
        "PYTHONUTF8 = \"1\"\n"
    )
    tomllib.loads(snippet)
    write_json(config_path, data)
    atomic_write(absolute_path(args.toml_output), snippet)
    print(f"Configuration: {config_path}")
    print(f"MCP client snippet: {absolute_path(args.toml_output)}")
    print(f"Workspace: {workspace}")
    print(f"COMSOL: {comsol or 'not found; specify --comsol-root if you will use COMSOL'}")
    print(f"LabVIEW: {labview or 'not found; optional unless you will use LabVIEW'}")
    print("Fusion: run .venv\\Scripts\\python.exe tools\\install_fusion_addin.py if required.")
    print("LabVIEW: install pywin32==312 in .venv, then run tools\\prepare_labview.py if required.")
    print("Script execution is disabled by default. Built-in tools remain available.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
