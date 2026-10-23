#!/usr/bin/env python3
"""Extract signed I and static I/V from one declared semiconductor I–V branch.

Standard-library-only. No branch sorting/merging, extrapolation, switching-state
inference, instrument control, external services, or model fitting.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import sys

VERSION = "0.1.0"
COLUMNS = {"device", "cycle", "branch", "voltage", "current"}
VOLTAGE_UNITS = {"V": "1", "mV": "0.001", "uV": "0.000001"}
CURRENT_UNITS = {"A": "1", "mA": "0.001", "uA": "0.000001", "nA": "0.000000001", "pA": "0.000000000001"}
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
ASSUMPTIONS = [
    "Voltage is the signed voltage across the measured device; current uses one consistent terminal/polarity convention.",
    "Each device/cycle/branch label denotes one acquisition-ordered monotonic-voltage segment and one fixed measurement protocol/state. Branch labels and state meaning are supplied by the user, not inferred.",
    "Interpolation assumes I is locally linear in V only between the adjacent samples reported. The software cannot verify this assumption, read disturb, settling, compliance clipping, noise floor, or temperature/protocol stability.",
    "Conductance_S is signed static I(V_read)/V_read, not differential dI/dV, a fitted conductance, or an intrinsic material property. Zero read voltage is rejected; negative current/conductance is retained.",
    "No interpolation across devices, cycles, or branches; no sorting to repair sweeps, extrapolation, SET/RESET threshold inference, or microscopic-mechanism inference.",
    "SI values are represented as finite binary64 JSON numbers. Nonzero values that underflow to zero and overflowing derived results are rejected. No measurement-uncertainty propagation or statistical independence is assumed.",
]


class InputError(ValueError):
    """A safe-to-print, deliberately non-data-bearing validation error."""


def finite_float(value):
    """Convert an exact/decimal value without silently returning inf or zero."""
    try:
        result = float(value)
    except (ValueError, OverflowError, DecimalException):
        raise InputError("numeric value is outside the supported finite range") from None
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise InputError("numeric value is outside the supported finite range")
    return result


def number(text, factor):
    value = text.strip()
    if len(value) > 128 or not NUMBER.fullmatch(value):
        raise InputError("invalid numeric field")
    try:
        with localcontext() as context:
            context.prec = 200
            original = Decimal(value)
            scaled = original * Decimal(factor)
            if original != 0 and scaled == 0:
                raise InputError("numeric value is outside the supported finite range")
            return finite_float(scaled)
    except (DecimalException, OverflowError, ValueError):
        raise InputError("invalid or unsupported numeric field") from None


def identifier(value):
    if not value or value != value.strip() or len(value) > 256:
        raise InputError("invalid device, cycle, or branch identifier")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InputError("invalid device, cycle, or branch identifier")
    return value


def load_csv(path, voltage_unit, current_unit):
    groups = {}
    rows = 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader, None)
            if header is None or len(header) != len(COLUMNS) or set(header) != COLUMNS:
                raise InputError("CSV header must contain exactly device, cycle, branch, voltage, current")
            for row in reader:
                # csv.reader represents an entirely blank physical line as [].
                if not row:
                    continue
                if len(row) != len(header):
                    raise InputError("CSV row width does not match the header")
                record = dict(zip(header, row))
                key = tuple(identifier(record[name]) for name in ("device", "cycle", "branch"))
                voltage = number(record["voltage"], VOLTAGE_UNITS[voltage_unit])
                current = number(record["current"], CURRENT_UNITS[current_unit])
                groups.setdefault(key, []).append((voltage, current, reader.line_num))
                rows += 1
    except (OSError, UnicodeError, csv.Error):
        raise InputError("unable to read a valid UTF-8 CSV input") from None
    if rows == 0:
        raise InputError("CSV contains no measurement rows")
    return groups, rows


def extract_group(key, points, read_voltage, max_span=None):
    result = dict(zip(("device", "cycle", "branch"), key))
    result.update({"sample_count": len(points), "status": "rejected"})
    voltages = [point[0] for point in points]
    if len(set(voltages)) != len(voltages):
        result["reason"] = "duplicate_voltage"
        return result
    ascending = all(b > a for a, b in zip(voltages, voltages[1:]))
    descending = all(b < a for a, b in zip(voltages, voltages[1:]))
    if not (ascending or descending):
        result["reason"] = "nonmonotonic_voltage"
        return result
    result["acquisition_direction"] = "single_point" if len(points) == 1 else ("increasing" if ascending else "decreasing")
    result["voltage_range_V"] = [min(voltages), max(voltages)]
    if not min(voltages) <= read_voltage <= max(voltages):
        result["reason"] = "read_voltage_outside_branch"
        return result
    selected = next((point for point in points if point[0] == read_voltage), None)
    if selected is not None:
        source = [selected]
        current = selected[1]
        method = "measured"
        span = 0.0
    else:
        ordered = points if ascending else list(reversed(points))
        source = next(([a, b] for a, b in zip(ordered, ordered[1:]) if a[0] < read_voltage < b[0]), None)
        if source is None:  # Defensive invariant: range + unique monotonic points imply a bracket.
            result["reason"] = "no_adjacent_bracket"
            return result
        low, high = source
        delta_v = Fraction(high[0]) - Fraction(low[0])
        if max_span is not None and delta_v > Fraction(max_span):
            result["reason"] = "bracket_span_exceeds_limit"
            return result
        weight = (Fraction(read_voltage) - Fraction(low[0])) / delta_v
        try:
            # Exact rational intermediate prevents overflow in (I_hi - I_lo).
            current = finite_float(Fraction(low[1]) + weight * (Fraction(high[1]) - Fraction(low[1])))
            span = finite_float(delta_v)
        except InputError:
            result["reason"] = "derived_numeric_range_error"
            return result
        method = "linear_interpolation"
    try:
        conductance = finite_float(Fraction(current) / Fraction(read_voltage))
    except InputError:
        result["reason"] = "derived_numeric_range_error"
        return result
    result.update({
        "status": "ok", "method": method, "read_voltage_V": read_voltage,
        "current_A": current, "conductance_S": conductance,
        "bracket_span_V": span, "source_rows": [point[2] for point in source],
        "bracket_voltage_V": [point[0] for point in source],
        "bracket_current_A": [point[1] for point in source],
    })
    return result


def evaluate(path, voltage_unit, current_unit, read_voltage, read_voltage_unit, branch=None, max_bracket_span=None):
    target = number(read_voltage, VOLTAGE_UNITS[read_voltage_unit])
    if target == 0:
        raise InputError("read voltage must be nonzero to calculate static conductance")
    span = None
    if max_bracket_span is not None:
        span = number(max_bracket_span, VOLTAGE_UNITS[read_voltage_unit])
        if span <= 0:
            raise InputError("maximum bracket span must be positive")
    groups, row_count = load_csv(path, voltage_unit, current_unit)
    selected = [(key, points) for key, points in groups.items() if branch is None or key[2] == branch]
    if not selected:
        raise InputError("branch filter matched no measurement group")
    results = [extract_group(key, points, target, span) for key, points in selected]
    rejected = sum(item["status"] == "rejected" for item in results)
    reasons = {}
    for item in results:
        if item["status"] == "rejected":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    return {
        "schema_version": 1, "tool": "iv-readpoint", "version": VERSION,
        "input_units": {"voltage": voltage_unit, "current": current_unit},
        "parameters": {"read_voltage_V": target, "max_bracket_span_V": span, "branch_filter": branch},
        "counts": {"input_rows": row_count, "input_groups": len(groups), "selected_groups": len(results),
                   "excluded_by_branch_filter": len(groups) - len(results), "extracted_groups": len(results) - rejected,
                   "rejected_groups": rejected, "rejected_by_reason": reasons},
        "assumptions_and_limits": ASSUMPTIONS,
        "results": results,
    }


def write_report(report, output=None):
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if output is None:
        sys.stdout.write(payload)
        return
    # O_EXCL refuses existing files, directories, and symlinks, including dangling ones.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "error: invalid command-line arguments; use --help for the required schema and options\n")


def parser():
    result = SafeParser(description="Extract signed current and static I/V at a read voltage within each explicit semiconductor I–V branch. No extrapolation or inferred switching states.")
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly device,cycle,branch,voltage,current")
    result.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS, help="Unit of the input voltage column")
    result.add_argument("--current-unit", required=True, choices=CURRENT_UNITS, help="Unit of the input current column")
    result.add_argument("--read-voltage", required=True, help="Signed nonzero read voltage (use --read-voltage=-0.1 for negatives)")
    result.add_argument("--read-voltage-unit", required=True, choices=VOLTAGE_UNITS, help="Unit of read voltage and optional maximum bracket span")
    result.add_argument("--branch", help="Exact user-declared branch label; otherwise process each branch separately")
    result.add_argument("--max-bracket-span", help="Optional positive bound on interpolation bracket width, in --read-voltage-unit")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new JSON report with owner-only permissions; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print raw identifiers, measurements, and derived features to stdout")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.voltage_unit, args.current_unit, args.read_voltage,
                          args.read_voltage_unit, args.branch, args.max_bracket_span)
        write_report(report, args.output)
    except InputError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("error: unable to write a new JSON report; existing outputs are never overwritten", file=sys.stderr)
        return 2
    return 1 if report["counts"]["rejected_groups"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
