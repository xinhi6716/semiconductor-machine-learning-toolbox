#!/usr/bin/env python3
"""Offline shared FET transfer input -> separate, auditable branch features.

Calls the existing extractors without reproducing their measurement algorithms.
Read README.md before adapting this strictly complete-branch example.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VERSION = "0.1.0"
TOOL_PATHS = {
    "vth": "tools/vth-constant-current/vth_constant_current.py",
    "log_slope": "tools/transfer-log-slope/transfer_log_slope.py",
}
FEATURE_COLUMNS = ("criterion_gate_source_voltage_V", "log_current_slope_decades_per_V")
GROUP_COLUMNS = ("lot", "wafer", "device", "condition", "replicate", "branch")
METADATA_COLUMNS = (*GROUP_COLUMNS, "split_group", "temperature_K", "drain_source_voltage_V",
                    "body_source_voltage_V", "signed_current_criterion_A", "window_min_V", "window_max_V",
                    "max_bracket_span_V", "branch_sample_count", "log_selected_sample_count",
                    "criterion_method", "criterion_bracket_span_V")
OUTPUT_COLUMNS = (*METADATA_COLUMNS, *FEATURE_COLUMNS)


class InputError(ValueError):
    """Safe diagnostics omit input identifiers, values, and paths."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_tool(name, relative):
    spec = importlib.util.spec_from_file_location("fet_features_" + name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vth = load_tool("vth", TOOL_PATHS["vth"])
log_slope = load_tool("log_slope", TOOL_PATHS["log_slope"])
LOADED_TOOL_HASHES = {relative: digest((ROOT / relative).read_bytes()) for relative in TOOL_PATHS.values()}


def read_regular(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InputError("input must be a regular CSV file")
            data = stream.read(min(vth.MAX_BYTES, log_slope.MAX_BYTES) + 1)
    except OSError:
        raise InputError("unable to read an input file") from None
    if len(data) > min(vth.MAX_BYTES, log_slope.MAX_BYTES):
        raise InputError("input exceeds the byte limit")
    return data


def csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")


def report_by_key(report):
    indexed = {}
    for result in report["results"]:
        key = tuple(result[name] for name in GROUP_COLUMNS)
        if key in indexed:
            raise InputError("extractor report contains duplicate branch keys")
        indexed[key] = result
    return indexed


def check_tools():
    if (vth.COLUMNS != log_slope.COLUMNS or vth.GROUP_COLUMNS != GROUP_COLUMNS
            or log_slope.GROUP_COLUMNS != GROUP_COLUMNS):
        raise InputError("extractor input schemas do not match this workflow")
    if any(digest((ROOT / path).read_bytes()) != value for path, value in LOADED_TOOL_HASHES.items()):
        raise InputError("extractor source changed after loading; restart the workflow")


def build(input_path, *, voltage_unit, current_unit, criterion_current, criterion_current_unit,
          current_polarity, max_bracket_span, window_min, window_max,
          assumptions_accepted=False):
    """Return complete output bytes, or fail before creating any output directory."""
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of both measurement assumption sets is required")
    if max_bracket_span is None:
        raise InputError("an explicit positive VGS interpolation bracket bound is required")
    check_tools()
    raw = read_regular(input_path)
    input_hash = digest(raw)
    # Both existing evaluate() APIs accept a path. A private snapshot guarantees
    # that both tools hash and consume the same original bytes, even if the source changes.
    try:
        with tempfile.TemporaryDirectory(prefix="fet-features-") as temporary:
            snapshot = Path(temporary) / "transfer.csv"
            fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            threshold = vth.evaluate(snapshot, voltage_unit, current_unit, criterion_current,
                                     criterion_current_unit, current_polarity, assumptions_accepted=True,
                                     branch=None, max_bracket_span=max_bracket_span)
            logarithmic = log_slope.evaluate(snapshot, voltage_unit, current_unit, window_min, window_max,
                                             current_polarity, assumptions_accepted=True)
    except (vth.InputError, log_slope.InputError) as error:
        raise InputError(str(error)) from None
    for report, tool in ((threshold, "vth-constant-current"), (logarithmic, "transfer-log-slope")):
        if report["schema_version"] != 1 or report["tool"] != tool:
            raise InputError("unsupported extractor report schema")
        if report["provenance"]["input_sha256"] != input_hash:
            raise InputError("extractor input fingerprint does not match the shared snapshot")
    threshold_by_key, logarithmic_by_key = report_by_key(threshold), report_by_key(logarithmic)
    if not threshold_by_key or threshold_by_key.keys() != logarithmic_by_key.keys():
        raise InputError("extractor branches do not match one-to-one")
    if (threshold["counts"]["input_rows"] != logarithmic["counts"]["input_rows"]
            or threshold["counts"]["excluded_by_branch_filter"] != 0
            or threshold["counts"]["input_groups"] != len(threshold_by_key)
            or logarithmic["counts"]["input_groups"] != len(logarithmic_by_key)):
        raise InputError("extractor counts do not match the complete shared input")
    rows, lineage, branch_statuses = [], [], []
    complete = all(item["status"] == "ok" for report in (threshold, logarithmic) for item in report["results"])
    for key, criterion_result in threshold_by_key.items():
        log_result = logarithmic_by_key[key]
        source_rows = [item["source_row"] for item in criterion_result["observations"]]
        if source_rows != log_result["source_rows"]:
            raise InputError("extractor source rows do not match")
        extraction = criterion_result["extraction"]
        branch_statuses.append({**dict(zip(GROUP_COLUMNS, key)),
                                "vth_status": criterion_result["status"],
                                "vth_reason": criterion_result.get("reason"),
                                "log_slope_status": log_result["status"],
                                "log_slope_reason": log_result.get("reason")})
        lineage.append({**dict(zip(GROUP_COLUMNS, key)),
                        "feature_csv_source_row": len(rows) + 2 if complete else None,
                        "predictor_csv_source_row": len(rows) + 2 if complete else None,
                        "transfer_source_rows": source_rows,
                        "criterion_source_rows": extraction["source_rows"] if extraction else [],
                        "log_selected_source_rows": log_result["selected_source_rows"],
                        "log_outside_window_source_rows": log_result["outside_window_source_rows"]})
        if not complete:
            continue
        if criterion_result["conditions"] != log_result["conditions"]:
            raise InputError("extractor fixed conditions do not match")
        # CSV safety is stricter than the tools' JSON-only identifier contract.
        if any(value[0] in "=+-@" for value in key):
            raise InputError("CSV export requires spreadsheet-safe identifiers")
        row = dict(zip(GROUP_COLUMNS, key))
        row.update(criterion_result["conditions"])
        row.update({"split_group": key[0],
                    "signed_current_criterion_A": threshold["parameters"]["signed_current_criterion_A"],
                    "window_min_V": logarithmic["parameters"]["window_min_V"],
                    "window_max_V": logarithmic["parameters"]["window_max_V"],
                    "max_bracket_span_V": threshold["parameters"]["max_bracket_span_V"],
                    "branch_sample_count": criterion_result["sample_count"],
                    "log_selected_sample_count": log_result["selected_sample_count"],
                    "criterion_method": extraction["method"],
                    "criterion_bracket_span_V": extraction["bracket_span_V"],
                    FEATURE_COLUMNS[0]: extraction["gate_source_voltage_at_criterion_V"],
                    FEATURE_COLUMNS[1]: log_result["fit"]["slope_decades_per_V"]})
        rows.append(row)
    check_tools()
    schema = {
        "schema_version": 1, "row_key": list(GROUP_COLUMNS),
        "model_feature_allowlist": list(FEATURE_COLUMNS), "metadata_columns": list(METADATA_COLUMNS),
        "target_columns": [], "feature_units": {FEATURE_COLUMNS[0]: "V", FEATURE_COLUMNS[1]: "decades/V"},
        "warnings": [
            "These two columns are candidate predictors, not a task-independent approval to use them.",
            "Never use a target or its algebraic proxy as an input feature. Redefine this allowlist for the actual endpoint.",
            "In particular, do not predict criterion VGS from that same criterion VGS, or slope from its reciprocal.",
            "Both features use the same transfer acquisition. Establish feature availability before a separately sourced endpoint.",
            "IDs, split groups, fixed biases, criterion/window choices, counts and extraction diagnostics are metadata, not model inputs.",
            "Log slope and its reciprocal are descriptive, not a certified subthreshold swing or identified physical regime.",
            "Rows are acquisition branches, not independent devices. Physical-device identity is lot/wafer/device.",
            "split_group equals lot as an example declaration, not evidence of independence between lots.",
            "No target, model, accuracy metric, uncertainty estimate or independent-device claim is supplied.",
        ],
    }
    status = {"schema_version": 1, "status": "complete" if complete else "audit_only",
              "feature_exported": complete, "branch_count": len(branch_statuses),
              "feature_row_count": len(rows), "branch_statuses": branch_statuses,
              "selection_policy": "all branches must pass both extractors; no partial-cohort feature export",
              "interpretation": "software completion only; no physical regime or ML suitability certified"}
    payloads = {
        "vth_report.json": json_bytes(threshold), "log_slope_report.json": json_bytes(logarithmic),
        "transfer_input.csv": raw, "workflow_status.json": json_bytes(status),
    }
    if complete:
        payloads.update({
            "features.csv": csv_bytes(OUTPUT_COLUMNS, rows),
            "predictors.csv": csv_bytes(FEATURE_COLUMNS, [{name: row[name] for name in FEATURE_COLUMNS} for row in rows]),
            "feature_schema.json": json_bytes(schema),
        })
    provenance = {
        "schema_version": 1, "workflow": "fet-measurement-to-features", "version": VERSION,
        "runtime": {"python": sys.version.split()[0]},
        "input_sha256": input_hash, "retained_input": "transfer_input.csv",
        "source_row_convention": threshold["provenance"]["source_row_convention"],
        "source_sha256": {**LOADED_TOOL_HASHES,
                          str(Path(__file__).resolve().relative_to(ROOT)): digest(Path(__file__).read_bytes())},
        "output_sha256": {name: digest(data) for name, data in payloads.items()},
        "counts": {"input_rows": threshold["counts"]["input_rows"], "branch_count": len(branch_statuses),
                   "feature_rows": len(rows), "distinct_physical_device_ids": len({key[:3] for key in threshold_by_key})},
        "row_unit": "one declared acquisition branch; not an independent device",
        "physical_device_key": list(GROUP_COLUMNS[:3]),
        "row_alignment": "features.csv and predictors.csv have identical data-row order, when present",
        "calculator_schema": "exact seven-field version-1 allowlist accepted by groupbench --features-file",
        "invocation": {"vth": "evaluate(..., branch=None); full original schema retained",
                       "log_slope": "evaluate(...); full original schema retained",
                       "measurement_assumptions_declared": True,
                       "input_units": threshold["input_units"], "vth_parameters": threshold["parameters"],
                       "log_slope_parameters": logarithmic["parameters"]},
        "acquisition_lineage": lineage,
    }
    payloads["provenance.json"] = json_bytes(provenance)
    return payloads


def write_outputs(output_dir, payloads):
    """Exclusive new directory only; an I/O failure may leave partial new files."""
    destination = Path(output_dir)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in payloads.items():
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "error: invalid arguments; see --help\n")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--voltage-unit", choices=tuple(vth.VOLTAGE_UNITS), required=True)
    parser.add_argument("--current-unit", choices=tuple(vth.CURRENT_UNITS), required=True)
    parser.add_argument("--criterion-current", required=True)
    parser.add_argument("--criterion-current-unit", choices=tuple(vth.CURRENT_UNITS), required=True)
    parser.add_argument("--current-polarity", choices=vth.CURRENT_POLARITIES, required=True)
    parser.add_argument("--max-bracket-span", required=True, help="Positive maximum bracket width, in --voltage-unit")
    parser.add_argument("--window-min", required=True, help="Inclusive VGS bound, in --voltage-unit")
    parser.add_argument("--window-max", required=True, help="Inclusive VGS bound, in --voltage-unit")
    parser.add_argument("--accept-measurement-assumptions", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        payloads = build(args.input, voltage_unit=args.voltage_unit, current_unit=args.current_unit,
                         criterion_current=args.criterion_current, criterion_current_unit=args.criterion_current_unit,
                         current_polarity=args.current_polarity, max_bracket_span=args.max_bracket_span,
                         window_min=args.window_min, window_max=args.window_max,
                         assumptions_accepted=args.accept_measurement_assumptions)
        write_outputs(args.output_dir, payloads)
    except InputError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("error: unable to create complete outputs; existing destinations are never overwritten", file=sys.stderr)
        return 2
    if json.loads(payloads["workflow_status.json"])["status"] == "audit_only":
        print("Created audit-only reports: at least one branch was rejected; no feature table or allowlist exported.")
        return 1
    print("Created separate FET branch features, numeric predictors and complete audits. No model was trained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
