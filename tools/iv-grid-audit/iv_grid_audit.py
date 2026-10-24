#!/usr/bin/env python3
"""Audit declared semiconductor I-V voltage-grid coverage, not response values.

Only explicit identity and voltage columns are decoded. Other fields are scanned
for CSV framing, never interpreted as numeric inputs or prediction targets.
"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata

GROUP_COLUMNS = ("lot", "wafer", "device", "condition", "sweep", "branch")
REQUIRED_COLUMNS = (*GROUP_COLUMNS, "sample_id", "voltage")
VOLTAGE_UNITS = ("V", "mV", "uV", "nV")
MAX_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 65536
MAX_COLUMNS = 64
MAX_ROWS = 10000
MAX_GROUPS = 1000
MAX_GRID_CELLS = 100000
MAX_REPORT_BYTES = 16 * 1024 * 1024
HEADER_NAME = re.compile(rb"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")


class InputError(ValueError):
    """Categorical error that never contains source values or paths."""


def _record(blob, position, selected):
    """Scan strict CSV framing; retain bytes only for selected field indices.

    selected=None is used only for the header. Opaque response values are not
    decoded, converted, validated, retained or emitted by this scanner.
    """
    start, width, kept = position, 0, {}
    while True:
        keep = selected is None or width in selected
        cell = bytearray() if keep else None
        if position < len(blob) and blob[position] == 34:
            position += 1
            while True:
                if position >= len(blob):
                    raise InputError("Unclosed CSV quote")
                char = blob[position]
                position += 1
                if char == 34:
                    if position < len(blob) and blob[position] == 34:
                        if keep:
                            cell.append(34)
                        position += 1
                    else:
                        break
                elif keep:
                    cell.append(char)
                if position - start > MAX_RECORD_BYTES:
                    raise InputError("CSV record byte limit exceeded")
            if position < len(blob) and blob[position] not in (44, 10, 13):
                raise InputError("Bytes after quoted CSV field")
        else:
            while position < len(blob) and blob[position] not in (44, 10, 13):
                char = blob[position]
                position += 1
                if char == 34:
                    raise InputError("Quote inside unquoted CSV field")
                if keep:
                    cell.append(char)
                if position - start > MAX_RECORD_BYTES:
                    raise InputError("CSV record byte limit exceeded")
        if keep:
            kept[width] = bytes(cell)
        width += 1
        if width > MAX_COLUMNS or position - start > MAX_RECORD_BYTES:
            raise InputError("CSV width or record limit exceeded")
        if position < len(blob) and blob[position] == 44:
            position += 1
            continue
        if position < len(blob) and blob[position] == 13:
            if position + 1 >= len(blob) or blob[position + 1] != 10:
                raise InputError("Bare CSV carriage return")
            position += 2
        elif position < len(blob) and blob[position] == 10:
            position += 1
        if position - start > MAX_RECORD_BYTES:
            raise InputError("CSV record byte limit exceeded")
        return kept, width, position


def _identity(raw):
    try:
        value = raw.decode("utf-8", "strict")
    except UnicodeError as error:
        raise InputError("Invalid identity UTF-8") from error
    if (not value or len(value) > 128 or value != value.strip()
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise InputError("Identity must be bounded, nonempty, unpadded and control-free")
    return value


def _voltage(raw):
    if len(raw) > 64:
        raise InputError("Voltage token length limit exceeded")
    try:
        text = raw.decode("ascii", "strict")
    except UnicodeError as error:
        raise InputError("Voltage must be an ASCII finite decimal") from error
    if not NUMBER.fullmatch(text):
        raise InputError("Voltage must be an unpadded finite decimal")
    value = Decimal(text)
    if len(value.as_tuple().digits) > 32 or abs(value.as_tuple().exponent) > 200:
        raise InputError("Voltage precision or stored exponent limit exceeded")
    return text, value


def parse_input(blob):
    if not isinstance(blob, bytes) or len(blob) > MAX_BYTES:
        raise InputError("CSV byte or type limit exceeded")
    position = 3 if blob.startswith(b"\xef\xbb\xbf") else 0
    fields, width, position = _record(blob, position, None)
    if any(not HEADER_NAME.fullmatch(raw) for raw in fields.values()):
        raise InputError("Invalid CSV header name")
    header = [fields[index].decode("ascii") for index in range(width)]
    if len(set(header)) != width or not set(REQUIRED_COLUMNS).issubset(header):
        raise InputError("Missing or duplicate required CSV header")
    selected = {header.index(name) for name in REQUIRED_COLUMNS}
    groups, row_count = {}, 0
    while position < len(blob):
        fields, row_width, position = _record(blob, position, selected)
        if row_width != width:
            raise InputError("CSV row width mismatch; blank records are not permitted")
        row_count += 1
        if row_count > MAX_ROWS:
            raise InputError("CSV row limit exceeded")
        record = {header[index]: raw for index, raw in fields.items()}
        key = tuple(_identity(record[name]) for name in GROUP_COLUMNS)
        sample_id = _identity(record["sample_id"])
        text, value = _voltage(record["voltage"])
        if key not in groups:
            if len(groups) >= MAX_GROUPS:
                raise InputError("Declared sweep-group limit exceeded")
            groups[key] = {"samples": [], "sample_ids": set()}
        group = groups[key]
        if sample_id in group["sample_ids"]:
            raise InputError("Duplicate sample ID within a declared sweep group")
        group["sample_ids"].add(sample_id)
        group["samples"].append({"source_row_ordinal": row_count, "sample_id": sample_id,
                                 "voltage_text": text, "value": value})
    if not row_count:
        raise InputError("CSV needs at least one data record")
    return header, groups, row_count


def audit(blob, voltage_unit):
    if voltage_unit not in VOLTAGE_UNITS:
        raise InputError("Explicit supported voltage unit required")
    header, groups, rows = parse_input(blob)
    comparisons, results = {}, []
    for key, group in groups.items():
        comparison_key = (key[3], key[5])
        comparison = comparisons.setdefault(comparison_key, {"union": {}, "groups": []})
        coordinates = {}
        for sample in group["samples"]:
            value = sample["value"]
            coordinates.setdefault(value, []).append(sample["source_row_ordinal"])
            first = comparison["union"].get(value)
            if first is None or sample["source_row_ordinal"] < first["source_row_ordinal"]:
                comparison["union"][value] = sample
        group["coordinates"] = coordinates
        group["key"] = key
        group["index"] = len(results) + 1
        comparison["groups"].append(group)
        results.append(group)
    # Prevent quadratic comparison expansion before constructing any grids.
    if sum(len(c["union"]) * len(c["groups"]) for c in comparisons.values()) > MAX_GRID_CELLS:
        raise InputError("Observed-union grid-cell limit exceeded")
    comparison_reports, group_reports = [], {}
    unequal_count, duplicate_groups = 0, 0
    for comparison_index, (key, comparison) in enumerate(comparisons.items(), 1):
        values = sorted(comparison["union"])
        coordinate_index = {value: index for index, value in enumerate(values, 1)}
        variants = {}
        for group in comparison["groups"]:
            coordinates = group["coordinates"]
            grid = tuple(sorted(coordinate_index[value] for value in coordinates))
            variants.setdefault(grid, []).append(group["index"])
            duplicates = [{"coordinate_index": coordinate_index[value], "source_row_ordinals": ordinals}
                          for value, ordinals in coordinates.items() if len(ordinals) > 1]
            duplicate_groups += bool(duplicates)
            group_reports[group["index"]] = {
                "group_index": group["index"], "identity": dict(zip(GROUP_COLUMNS, group["key"])),
                "comparison_index": comparison_index,
                "samples": [{name: value for name, value in sample.items() if name != "value"}
                            | {"coordinate_index": coordinate_index[sample["value"]]}
                            for sample in group["samples"]],
                "unique_coordinate_indices": list(grid),
                "duplicate_coordinates": duplicates,
                "missing_from_observed_union_indices": [coordinate_index[value] for value in values if value not in coordinates]}
        unequal = len(variants) > 1
        unequal_count += unequal
        comparison_reports.append({
            "comparison_index": comparison_index, "condition": key[0], "branch": key[1],
            "group_indices": [group["index"] for group in comparison["groups"]],
            "observed_union": [{"coordinate_index": coordinate_index[value],
                                "first_observed_voltage_text": comparison["union"][value]["voltage_text"]} for value in values],
            "grid_variants": [{"coordinate_indices": list(grid), "group_indices": indices}
                              for grid, indices in variants.items()], "unequal_grids": unequal})
    findings = bool(duplicate_groups or unequal_count)
    report = {
        "schema_version": "iv-grid-audit-v1",
        "status": "GRID_FINDINGS" if findings else "NO_GRID_FINDINGS",
        "provenance": {"csv_sha256": hashlib.sha256(blob).hexdigest(), "csv_bytes": len(blob),
                       "row_ordinal_convention": "one-based logical data record, excluding header"},
        "selection": {"decoded_columns": list(REQUIRED_COLUMNS),
                      "opaque_columns": [name for name in header if name not in REQUIRED_COLUMNS],
                      "response_values_inspected": False},
        "parameters": {"voltage_unit_declared": voltage_unit, "unit_conversion": "none",
                       "comparison": "exact_decimal_set", "comparison_columns": ["condition", "branch"]},
        "counts": {"input_rows": rows, "declared_sweep_groups": len(groups),
                   "comparison_sets": len(comparisons), "groups_with_duplicate_coordinates": duplicate_groups,
                   "comparison_sets_with_unequal_grids": unequal_count},
        "groups": [group_reports[index] for index in range(1, len(groups) + 1)],
        "comparisons": comparison_reports,
        "limitations": [
            "Only voltage and required identities are decoded; other values are scanned for CSV framing only.",
            "Condition and branch are exact global comparison labels, not verified common physical protocols.",
            "A declared sweep group is not a verified physical DUT, acquisition or independent observation.",
            "Missing means absent from the observed union, not from a planned or physically required grid.",
            "A coordinate missing from every supplied group and a wholly absent group cannot be detected.",
            "Set equality ignores order and does not validate chronology, direction, hysteresis or interpolation.",
            "Duplicate coordinates may be intentional repeated observations; none are dropped or averaged.",
            "Units, measurement quality, response/target eligibility and fitness for modeling are not verified."]}
    report_bytes(report)
    return report


def read_regular(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise InputError("Input must be a bounded regular file")
            blob = stream.read(MAX_BYTES + 1)
    except (OSError, ValueError) as error:
        if isinstance(error, InputError):
            raise
        raise InputError("Cannot read requested regular input") from error
    if len(blob) > MAX_BYTES:
        raise InputError("Input byte limit exceeded")
    return blob


def report_bytes(report):
    payload = (json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        raise InputError("Complete report byte limit exceeded")
    return payload


def write_report(payload, path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def write_stdout(payload):
    stream = sys.stdout
    if stream is None:
        raise InputError("Output stream unavailable")
    try:
        stream.write(payload.decode("utf-8"))
        stream.flush()
    except (OSError, ValueError):
        try:
            stream.close()
        except (OSError, ValueError):
            pass
        raise


def emit_failure(message):
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        try:
            stream.close()
        except (OSError, ValueError):
            pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("Invalid command-line options; use --help")


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("csv", help="Local I-V CSV; only declared identity and voltage fields are decoded")
    parser.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--stdout", action="store_true", help="Explicitly print raw identities and voltage grids")
    destination.add_argument("--output", help="New report path; never overwrite")
    try:
        args = parser.parse_args(argv)
        report = audit(read_regular(args.csv), args.voltage_unit)
        payload = report_bytes(report)
        if args.stdout:
            write_stdout(payload)
        else:
            write_report(payload, args.output)
        return 1 if report["status"] == "GRID_FINDINGS" else 0
    except InputError as error:
        emit_failure("Audit failed: " + str(error) + ". No complete report claimed.")
    except (OSError, ValueError, UnicodeError):
        emit_failure("Audit failed: input/output/serialization error. No complete report claimed.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
