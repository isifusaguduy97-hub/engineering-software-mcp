from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Config:
    workspace: Path
    comsol_root: Path | None
    labview_root: Path | None
    fusion_runtime: Path
    labview_runtime: Path
    enable_scripts: bool = False
    max_job_seconds: int = 1800

    @classmethod
    def load(cls, filename: str | Path | None = None) -> 'Config':
        file = Path(filename or os.environ.get('ENGINEERING_MCP_CONFIG', PROJECT / 'config.local.json')).resolve()
        if not file.is_file():
            raise FileNotFoundError('Configuration missing. Run Setup.cmd first, or supply --config FILE.')
        raw = json.loads(file.read_text(encoding='utf-8-sig'))
        def path(value, default=None):
            if not value: return default
            p = Path(value).expanduser()
            return (p if p.is_absolute() else file.parent / p).resolve()
        workspace = path(raw.get('workspace'), file.parent)
        if not isinstance(raw.get('enable_scripts', False), bool):
            raise ValueError('enable_scripts must be a JSON boolean')
        limit = raw.get('max_job_seconds', 1800)
        if not isinstance(limit, int) or not 30 <= limit <= 86400:
            raise ValueError('max_job_seconds must be 30..86400')
        return cls(workspace, path(raw.get('comsol_root')), path(raw.get('labview_root')),
                   path(raw.get('fusion_runtime'), workspace / 'work' / 'fusion-runtime'),
                   path(raw.get('labview_runtime'), workspace / 'work' / 'labview-runtime'),
                   raw.get('enable_scripts', False), limit)

    def environment(self):
        env = os.environ.copy()
        env.update(ENGINEERING_MCP_WORKSPACE=str(self.workspace),
                   ENGINEERING_MCP_FUSION_RUNTIME=str(self.fusion_runtime),
                   ENGINEERING_MCP_LABVIEW_RUNTIME=str(self.labview_runtime),
                   PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        return env

    def input_file(self, filename: str, suffixes: tuple[str, ...]) -> Path:
        p = Path(filename)
        p = (p if p.is_absolute() else self.workspace / p).resolve(strict=True)
        p.relative_to(self.workspace.resolve())
        if not p.is_file() or p.suffix.lower() not in suffixes:
            raise ValueError('Unsupported input file. Copy a supported file into workspace/inputs first.')
        return p
