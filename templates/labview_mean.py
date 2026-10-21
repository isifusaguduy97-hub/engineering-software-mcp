"""Call the installed NI Mean VI through the host's already typed COM object.

This invokes an existing VI. It neither changes the NI library nor claims to
generate a new LabVIEW block diagram. PARAMETERS/OUTPUT_DIR are adapter literals.
"""
from pathlib import Path
import csv
import json
import math
import pythoncom
from win32com.client import VARIANT

PARAMETERS = None
OUTPUT_DIR = None


def run(context):
    if not isinstance(PARAMETERS, dict) or OUTPUT_DIR is None:
        raise ValueError("The MCP adapter must supply PARAMETERS and OUTPUT_DIR")
    source = PARAMETERS.get("values")
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError("values must be a nonempty numeric array")
    if len(source) > 100000:
        raise ValueError("At most 100000 numbers are supported")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in source):
        raise ValueError("Every value must be a number")
    values = [float(item) for item in source]
    if any(not math.isfinite(item) for item in values):
        raise ValueError("Every value must be finite")
    directory = Path(OUTPUT_DIR).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "input_values.csv"
    report_path = directory / "verification.json"
    for path in (csv_path, report_path):
        if path.exists():
            raise FileExistsError("An output already exists: " + str(path))
    lv = context["lv"]
    vi_path = Path(lv.ApplicationDirectory) / "vi.lib" / "Analysis" / "baseanly.llb" / "Mean (DBL).vi"
    if not vi_path.is_file() and not vi_path.parent.is_file():
        # An LLB is a file containing VIs, so the individual VI path need not
        # satisfy Path.is_file even though GetVIReference can open it.
        raise FileNotFoundError("The installed Mean VI / LLB was not found: " + str(vi_path))
    vi = lv.GetVIReference(str(vi_path), "", False, 0)
    vi.SetControlValue("X", VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, values))
    vi.Run(False)
    mean = float(vi.GetControlValue("mean"))
    error_code = int(vi.GetControlValue("error"))
    if error_code:
        raise RuntimeError(f"LabVIEW Mean VI returned error code {error_code}")
    expected = math.fsum(value / len(values) for value in values)
    if not math.isfinite(mean) or not math.isclose(mean, expected, rel_tol=1e-10, abs_tol=1e-12):
        raise RuntimeError(f"Mean verification failed: LabVIEW={mean}, reference={expected}")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["index", "value"])
        writer.writerows(enumerate(values, 1))
    report = {"software": "LabVIEW", "version": str(lv.Version),
        "numeric_vi": str(vi.Name), "vi_path": str(vi_path),
        "input_count": len(values), "input_values": values, "mean": mean,
        "reference_mean": expected, "absolute_error": abs(mean - expected),
        "error_code": error_code, "checks_passed": True,
        "operation": "invoke_existing_vi", "created_new_block_diagram": False,
        "files": {"input_csv": str(csv_path), "verification": str(report_path)}}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
