"""Local COMSOL Java/batch bridge. Requires Python 3.10+ and licensed COMSOL."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime
from uuid import uuid4

DEFAULT_ROOT = Path(r"E:\COMSOL64\Multiphysics")

def _bridge_settings():
    config_file = Path(os.environ.get("ENGINEERING_MCP_CONFIG", Path(__file__).resolve().parents[2] / "config.local.json"))
    return json.loads(config_file.read_text(encoding="utf-8-sig")) if config_file.is_file() else {}

_settings = _bridge_settings()
WORKSPACE = Path(os.environ.get("ENGINEERING_MCP_WORKSPACE", _settings.get("workspace", Path(__file__).resolve().parents[2]))).resolve()



def launch(executable: Path, arguments: list[str], job: Path, name: str) -> None:
    stdout = job / f"{name}.stdout.log"
    stderr = job / f"{name}.stderr.log"
    environment = os.environ.copy()
    java_home = job / "java-user-home"
    java_home.mkdir(exist_ok=True)
    # Child Java processes also need a writable home for their own defaults.
    inherited_options = environment.get("JAVA_TOOL_OPTIONS", "")
    environment["JAVA_TOOL_OPTIONS"] = inherited_options + ' "-Duser.home=' + str(java_home) + '"'
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        result = subprocess.run(
            [str(executable), *arguments], cwd=job, stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=environment,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"{name} exited with code {result.returncode}. Logs: {stdout}, {stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comsol-root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagnose")
    run = sub.add_parser("run")
    run.add_argument("input", type=Path, help="Java source, compiled class, or MPH model")
    run.add_argument("--output", type=Path, required=True, help="New output MPH file; never overwrites")
    run.add_argument("--study", help="Study tag for MPH input, for example std1")
    run.add_argument("--cores", type=int, default=2)
    args = parser.parse_args()

    root = args.comsol_root.resolve()
    batch = root / "bin" / "win64" / "comsolbatch.exe"
    # Use the shipped javac with the documented apiplugins classpath. This also
    # avoids launcher quoting failures in comsolcompile on this installation.
    compiler = root / "java" / "win64" / "jre" / "bin" / "javac.exe"
    for executable in (batch, compiler):
        if not executable.is_file():
            raise FileNotFoundError(executable)

    # Eclipse/COMSOL defaults point outside the writable workspace. Set every
    # mutable startup directory explicitly, including configuration and data.
    job = WORKSPACE / "work" / "comsol-runtime" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    job.mkdir(parents=True)
    common: list[str] = []
    for option, folder in (("-configuration", "configuration"), ("-data", "data"),
                           ("-prefsdir", "prefs"), ("-tmpdir", "tmp"),
                           ("-recoverydir", "recovery")):
        directory = job / folder
        directory.mkdir()
        common += [option, str(directory)]
    common += ["-migrateprefs", "off", "-autosave", "off"]

    if args.command == "diagnose":
        launch(batch, common + ["-version"], job, "version")
        version_text = (job / "version.stdout.log").read_text(encoding="utf-8", errors="replace")
        if "COMSOL Multiphysics" not in version_text:
            raise RuntimeError(f"No COMSOL version returned; inspect {job}")
        print(json.dumps({"status": "launcher_ok", "root": str(root), "logs": str(job),
                          "note": "A successful model run is still required to verify licensing and solving."}, ensure_ascii=False))
        return

    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if source.suffix.lower() not in (".java", ".class", ".mph"):
        raise ValueError("Input must be .java, .class, or .mph")
    if output.suffix.lower() != ".mph":
        raise ValueError("Output must have the .mph extension")
    if output.exists():
        raise FileExistsError(f"Choose a new output filename: {output}")
    if args.cores < 1:
        raise ValueError("--cores must be positive")
    if args.study and source.suffix.lower() != ".mph":
        raise ValueError("--study is only supported for MPH input")
    output.parent.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".java":
        staged = job / source.name
        shutil.copy2(source, staged)
        compiler_args = ["-encoding", "UTF-8", "-classpath", str(root / "apiplugins" / "*"), str(staged)]
        launch(compiler, compiler_args, job, "compile")
        source = staged.with_suffix(".class")
        if not source.is_file():
            raise RuntimeError(f"Compilation did not create {source}; inspect {job}")

    staged_output = job / "result.mph"
    batch_args = common + ["-np", str(args.cores), "-inputfile", str(source),
                           "-outputfile", str(staged_output), "-batchlog", str(job / "batch.log")]
    if args.study:
        batch_args += ["-study", args.study]
    launch(batch, batch_args, job, "batch")
    transcript = (job / "batch.log").read_text(encoding="utf-8", errors="replace") if (job / "batch.log").exists() else ""
    if re.search(r"(?im)^(?:/\*+\s*(?:error|错误)|\s*ERROR:)", transcript):
        raise RuntimeError(f"COMSOL reported an error; inspect {job / 'batch.log'}")
    candidates = list(job.glob("result*.mph"))
    if len(candidates) != 1 or candidates[0].stat().st_size == 0:
        raise RuntimeError(f"COMSOL did not create the output model; inspect {job}")
    if output.exists():
        raise FileExistsError(f"Output appeared during the run; preserving it: {output}")
    shutil.move(str(candidates[0]), str(output))
    print(json.dumps({"status": "model_saved", "output": str(output),
                      "bytes": output.stat().st_size, "logs": str(job)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
