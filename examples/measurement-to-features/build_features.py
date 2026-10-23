#!/usr/bin/env python3
"""Strict, offline RRAM example: declared state readpoints -> device features.

Imports the existing measurement tools; does not reproduce their extraction or
pairing algorithms. See README.md before adapting the invented input protocol.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VERSION = "0.1.0"
MANIFEST_COLUMNS = ("batch", "wafer", "device", "cycle", "replicate", "branch", "state", "condition")
CONDITION_COLUMNS = ("condition", "read_voltage_V", "temperature_K", "read_delay_ms", "protocol")
IV_COLUMNS = ("device", "cycle", "branch", "voltage", "current")
INPUT_FILES = ("manifest.csv", "conditions.csv", "iv_sweeps.csv")
FEATURE_COLUMNS = ("hrs_resistance_median_ohm", "lrs_resistance_median_ohm", "resistance_ratio_median")
OUTPUT_COLUMNS = ("batch", "wafer", "device", "condition", "split_group", "read_voltage_V", "cycle_count",
                  "ratio_below_one_cycle_count", *FEATURE_COLUMNS)
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")


class InputError(ValueError):
    """Diagnostic messages never contain input values, IDs, or paths."""


def load_tool(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # Required by the RRAM tool's dataclass annotation.
    spec.loader.exec_module(module)
    return module


iv = load_tool("measurement_features_iv", "tools/iv-readpoint/iv_readpoint.py")
rram = load_tool("measurement_features_rram", "tools/rram-window/rram_window.py")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise InputError("input must be a regular file")
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError:
        raise InputError("unable to read an input file") from None
    if len(data) > MAX_BYTES:
        raise InputError("input exceeds the byte limit")
    return data


def parse_csv(data, columns):
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, None)
        if header is None or len(header) != len(columns) or set(header) != set(columns):
            raise InputError("CSV header must contain exactly the documented columns")
        rows = []
        for values in reader:
            if not values:
                continue
            if len(values) != len(header):
                raise InputError("CSV row width does not match the header")
            record = dict(zip(header, values))
            record["source_row"] = reader.line_num
            rows.append(record)
            if len(rows) > MAX_ROWS:
                raise InputError("CSV exceeds the row limit")
    except (UnicodeError, csv.Error):
        raise InputError("input must be valid UTF-8 CSV") from None
    if not rows:
        raise InputError("CSV contains no data rows")
    return rows


def identifier(value):
    if (not value or value != value.strip() or len(value) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or value[0] in "=+-@"):
        raise InputError("identifiers must be unpadded, control-free and spreadsheet-safe")
    return value


def numeric(value):
    """Bound decimals before conversion; keep exact condition declarations."""
    raw = value.strip()
    if len(raw) > 64 or not NUMBER.fullmatch(raw):
        raise InputError("invalid numeric field")
    try:
        exact = Decimal(raw)
        if len(exact.as_tuple().digits) > 32 or abs(exact.as_tuple().exponent) > 200:
            raise InputError("numeric precision or exponent exceeds example limits")
        result = float(exact)
    except (InvalidOperation, ValueError, OverflowError):
        raise InputError("numeric field exceeds the supported range") from None
    if not math.isfinite(result) or (exact != 0 and result == 0):
        raise InputError("numeric field exceeds the supported range")
    return exact


def csv_text(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"


def median(values):
    """Descriptive within-device median, avoiding midpoint overflow."""
    values = sorted(values)
    n = len(values)
    if n % 2:
        return values[n // 2]
    return iv.finite_float((Fraction(values[n // 2 - 1]) + Fraction(values[n // 2])) / 2)


def build(input_dir, *, max_bracket_span_V, assumptions_accepted=False):
    """Return output texts in memory; write nothing if any measurement/join fails."""
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of measurement assumptions is required")
    span = numeric(str(max_bracket_span_V))
    if span <= 0:
        raise InputError("maximum bracket span must be positive")
    raw = {name: read_regular(Path(input_dir) / name) for name in INPUT_FILES}
    manifest = parse_csv(raw["manifest.csv"], MANIFEST_COLUMNS)
    condition_rows = parse_csv(raw["conditions.csv"], CONDITION_COLUMNS)
    measurements = parse_csv(raw["iv_sweeps.csv"], IV_COLUMNS)
    conditions = {}
    for row in condition_rows:
        key = identifier(row["condition"])
        identifier(row["protocol"])
        if key in conditions:
            raise InputError("duplicate condition declaration")
        for field in ("read_voltage_V", "temperature_K", "read_delay_ms"):
            numeric(row[field])
        if (numeric(row["read_voltage_V"]) == 0 or numeric(row["temperature_K"]) <= 0
                or numeric(row["read_delay_ms"]) < 0):
            raise InputError("invalid read voltage, temperature, or read delay declaration")
        conditions[key] = row
    indexed, pairs, devices, replicate_keys = {}, {}, {}, {}
    for row in manifest:
        for field in MANIFEST_COLUMNS:
            identifier(row[field])
        if row["state"] not in ("HRS", "LRS"):
            raise InputError("state must be explicitly HRS or LRS")
        key = tuple(row[field] for field in ("device", "cycle", "branch"))
        if key in indexed:
            raise InputError("duplicate manifest measurement key")
        indexed[key] = row
        if row["condition"] not in conditions:
            raise InputError("manifest condition has no declaration")
        identity = tuple(row[field] for field in ("batch", "wafer", "condition"))
        if devices.setdefault(row["device"], identity) != identity:
            raise InputError("device identity or condition changes across repeated acquisitions")
        pair_key = (row["device"], row["cycle"])
        pair = pairs.setdefault(pair_key, {})
        if row["state"] in pair:
            raise InputError("duplicate state for a device/cycle pair")
        pair[row["state"]] = row
        repeat_key = (row["device"], row["replicate"])
        if replicate_keys.setdefault(repeat_key, row["cycle"]) != row["cycle"]:
            raise InputError("replicate identifier is reused across different cycles")
    if {row["condition"] for row in manifest} != set(conditions):
        raise InputError("unused condition declaration")
    for pair in pairs.values():
        if set(pair) != {"HRS", "LRS"}:
            raise InputError("manifest must declare one HRS and one LRS branch per cycle")
        if pair["HRS"]["replicate"] != pair["LRS"]["replicate"]:
            raise InputError("HRS and LRS replicate declarations do not match")
    actual_keys = set()
    for row in measurements:
        for field in IV_COLUMNS[:3]:
            identifier(row[field])
        for field in IV_COLUMNS[3:]:
            numeric(row[field])
        actual_keys.add(tuple(row[field] for field in IV_COLUMNS[:3]))
    if actual_keys != set(indexed):
        raise InputError("measurement and manifest keys must match exactly; missing or extra acquisition")

    # One bounded snapshot is used for both tool execution and the provenance
    # hash, rather than reopening user input between validation and extraction.
    with tempfile.TemporaryDirectory(prefix="rram-features-") as directory:
        snapshot = Path(directory) / "iv.csv"
        snapshot.write_bytes(raw["iv_sweeps.csv"])
        try:
            groups, row_count = iv.load_csv(snapshot, "V", "A")
            results = []
            resistance_rows, lineage = [], []
            for key in sorted(indexed):
                declaration = indexed[key]
                condition = conditions[declaration["condition"]]
                target = iv.number(condition["read_voltage_V"], "1")
                result = iv.extract_group(key, groups[key], target, float(span))
                if result["status"] != "ok":
                    raise InputError("I-V extraction rejected an acquisition; inspect the original branch with iv-readpoint")
                current = result["current_A"]
                if current == 0 or (current > 0) != (target > 0):
                    raise InputError("V/I requires nonzero current with the same sign as read voltage")
                resistance = iv.finite_float(Fraction(target) / Fraction(current))
                if resistance <= 0:
                    raise InputError("V/I resistance must be positive and finite")
                results.append(result)
                resistance_rows.append({"device": key[0], "cycle": key[1], "state": declaration["state"],
                                        "read_condition": declaration["condition"],
                                        "read_voltage": condition["read_voltage_V"], "resistance": repr(resistance)})
                lineage.append({"device": key[0], "cycle": key[1], "branch": key[2],
                                "replicate": declaration["replicate"], "state": declaration["state"],
                                "condition": declaration["condition"],
                                "manifest_source_row": declaration["source_row"],
                                "condition_source_row": condition["source_row"],
                                "iv_source_rows": result["source_rows"],
                                "resistance_input_source_row": len(resistance_rows) + 1})
        except iv.InputError:
            raise InputError("I-V input or derived resistance exceeds the upstream tool's supported range") from None
    resistance_csv = csv_text(rram.COLUMNS, resistance_rows)
    try:
        windows = rram.evaluate_stream(io.StringIO(resistance_csv), voltage_unit="V", resistance_unit="ohm",
                                       hrs_label="HRS", lrs_label="LRS", source="resistance_input.csv")
    except rram.InputError:
        raise InputError("derived resistance input was rejected by rram-window") from None
    if windows["counts"]["excluded_groups"]:
        raise InputError("rram-window excluded a declared pair")
    window_keys = {(pair["device"], pair["cycle"]) for pair in windows["pairs"]}
    if window_keys != set(pairs) or len(windows["pairs"]) != len(pairs):
        raise InputError("RRAM pairing did not preserve the declared acquisition set")
    features, device_lineage = [], []
    for device, (batch, wafer, condition_id) in sorted(devices.items()):
        observations = [pair for pair in windows["pairs"] if pair["device"] == device]
        features.append({"batch": batch, "wafer": wafer, "device": device, "condition": condition_id,
                         "split_group": batch, "read_voltage_V": conditions[condition_id]["read_voltage_V"],
                         "cycle_count": len(observations),
                         "ratio_below_one_cycle_count": sum(pair["ratio_below_one"] for pair in observations),
                         "hrs_resistance_median_ohm": median([pair["hrs"]["resistance_ohm"] for pair in observations]),
                         "lrs_resistance_median_ohm": median([pair["lrs"]["resistance_ohm"] for pair in observations]),
                         "resistance_ratio_median": median([pair["resistance_ratio"] for pair in observations])})
        device_lineage.append({"device": device, "cycles": sorted(pair["cycle"] for pair in observations),
                               "replicates": sorted({row["replicate"] for row in manifest if row["device"] == device})})
    provenance = {"schema_version": 1, "example_version": VERSION, "python_version": sys.version.split()[0],
                  "inputs_sha256": {name: digest(data) for name, data in raw.items()},
                  "code_sha256": {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in
                                  (Path(__file__), ROOT / "tools/iv-readpoint/iv_readpoint.py",
                                   ROOT / "tools/rram-window/rram_window.py")},
                  "parameters": {"max_bracket_span_V_input": str(max_bracket_span_V),
                                 "input_voltage_unit": "V", "input_current_unit": "A",
                                 "measurement_assumptions_declared": True},
                  "conversion": "R_static_ohm = signed V_read_V / signed I_read_A; no absolute values or state inference",
                  "aggregation": "median within each device and fixed condition, never across devices",
                  "independent_unit": "user-declared physical device; independence is not verified",
                  "split_group": "batch; examples provide grouping metadata, not a train/test split",
                  "condition_declarations": [conditions[key] for key in sorted(conditions)],
                  "device_lineage": device_lineage, "acquisition_lineage": lineage}
    schema = {"schema_version": 1, "row_key": ["device"], "model_feature_allowlist": list(FEATURE_COLUMNS),
              "metadata_columns": [name for name in OUTPUT_COLUMNS if name not in FEATURE_COLUMNS],
              "target_columns": [], "feature_units": {FEATURE_COLUMNS[0]: "ohm", FEATURE_COLUMNS[1]: "ohm",
                                                        FEATURE_COLUMNS[2]: "dimensionless"},
              "warnings": ["No target or trained model is included.",
                           "The ratio is derived from the same HRS/LRS observations; do not predict it from those resistances.",
                           "Counts, IDs, batch and condition are audit/splitting metadata, not default model inputs.",
                           "A row per declared device is not proof of statistical independence."]}
    iv_report = {"schema_version": 1, "tool": "iv-readpoint", "version": iv.VERSION,
                 "invocation": "load_csv(V,A) and extract_group per manifest condition; no branch filter",
                 "input_rows": row_count, "max_bracket_span_V": float(span),
                 "assumptions_and_limits": iv.ASSUMPTIONS, "results": results}
    return {"features.csv": csv_text(OUTPUT_COLUMNS, features), "feature_schema.json": json_text(schema),
            "iv_readpoints.json": json_text(iv_report), "resistance_input.csv": resistance_csv,
            "rram_windows.json": json_text(windows), "provenance.json": json_text(provenance)}


def write_outputs(output_dir, payloads):
    """New directory only. Validation precedes creation; I/O failure may be partial."""
    destination = Path(output_dir)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, text in payloads.items():
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "error: invalid arguments; see --help\n")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True,
                        help="Three input CSVs; iv_sweeps.csv voltage is V and current is A (SI only)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Create a new private directory; never overwrite")
    parser.add_argument("--max-bracket-span-V", required=True, help="Explicit positive readpoint bracket bound in volts")
    parser.add_argument("--accept-measurement-assumptions", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        payloads = build(args.input_dir, max_bracket_span_V=args.max_bracket_span_V,
                         assumptions_accepted=args.accept_measurement_assumptions)
        write_outputs(args.output_dir, payloads)
    except InputError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("error: unable to create complete outputs; existing destinations are never overwritten", file=sys.stderr)
        return 2
    print("Created a device-level feature table and five provenance/schema reports. No model was trained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
