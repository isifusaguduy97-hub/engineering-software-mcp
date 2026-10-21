"""Small, standard-library-only helpers for explicit local setup commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT / "config.local.json"


def absolute_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def read_config(path: Path = DEFAULT_CONFIG) -> dict:
    path = absolute_path(path)
    if not path.is_file():
        raise ValueError(f"Configuration does not exist: {path}. Run Setup.cmd first.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data.get("workspace"):
        raise ValueError(f"Invalid configuration (workspace is required): {path}")
    return data


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
