"""Explicitly install a separate EngineeringMCPFusionBridge for this Windows user."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from _common import DEFAULT_CONFIG, PROJECT, absolute_path, read_config, write_json

NAME = "EngineeringMCPFusionBridge"
SOURCE_NAME = "CodexFusionBridge"


def fingerprints(folder: Path) -> dict[str, str]:
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob("*") if path.is_file()
            and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--addins-dir", help="Override the user Fusion AddIns folder (advanced/testing)")
    args = parser.parse_args(argv)
    config = read_config(absolute_path(args.config))
    source = PROJECT / "bridges" / "fusion" / SOURCE_NAME
    if not (source / f"{SOURCE_NAME}.py").is_file():
        raise ValueError(f"Bundled add-in source is missing: {source}")
    if args.addins_dir:
        base = absolute_path(args.addins_dir)
    else:
        if not os.environ.get("APPDATA"):
            raise ValueError("APPDATA is unavailable. Run this tool in your normal Windows desktop session.")
        base = absolute_path(Path(os.environ["APPDATA"]) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns")
    base.mkdir(parents=True, exist_ok=True)
    target = (base / NAME).resolve()
    # The tool only replaces its own named add-in, never an existing Codex add-in.
    if target.parent != base or target.name != NAME or target.is_symlink():
        raise ValueError("Unsafe add-in installation destination.")
    stage = base / f".{NAME}.install-{uuid.uuid4().hex}"
    backup = None
    try:
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "bridge-config.json"))
        for path in sorted(stage.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".manifest"):
                content = path.read_text(encoding="utf-8-sig").replace(SOURCE_NAME, NAME)
                path.write_text(content, encoding="utf-8", newline="\n")
                if SOURCE_NAME in path.name:
                    path.rename(path.with_name(path.name.replace(SOURCE_NAME, NAME)))
        manifest_path = stage / f"{NAME}.manifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(author="Engineering Software MCP contributors", runOnStartup=False)
        manifest["description"] = {"": "Local engineering-software-mcp connector; run manually after checking your workspace."}
        write_json(manifest_path, manifest)
        write_json(stage / "bridge-config.json", {
            "workspace": str(absolute_path(config["workspace"])),
            "fusion_runtime": str(absolute_path(config.get("fusion_runtime")
                                  or Path(config["workspace"]) / "work" / "fusion-runtime")),
            "enable_scripts": bool(config.get("enable_scripts", False)),
        })
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"Expected an add-in folder, but found a file: {target}")
            if fingerprints(target) == fingerprints(stage):
                print(f"Already installed with the same configuration: {target}")
                return 0
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            backup = base / f"{NAME}.backup-{suffix}"
            if backup.resolve().parent != base:
                raise ValueError("Unsafe backup destination.")
            target.rename(backup)
        stage.rename(target)
    except Exception:
        if backup and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        if stage.exists():
            # stage is a literal child created by this operation, checked above.
            if stage.resolve().parent == base and stage.name.startswith(f".{NAME}.install-"):
                shutil.rmtree(stage)
    print(f"Installed: {target}")
    if backup:
        print(f"Previous copy preserved: {backup}")
    print(f"Open Fusion normally. Press Shift+S, open Add-Ins, and run {NAME}.")
    print("If it was already running, stop and restart this add-in. The original CodexFusionBridge is untouched.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Fusion add-in installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
