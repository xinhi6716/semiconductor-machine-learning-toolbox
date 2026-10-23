#!/usr/bin/env python3
"""Describe paired, user-labelled RRAM resistance windows without inference.

Standard-library-only. See README.md for the strict CSV contract and limits.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import TextIO


SCHEMA_VERSION = "1.0"
COLUMNS = ("device", "cycle", "state", "read_condition", "read_voltage", "resistance")
VOLTAGE_UNITS = {"V": 1.0, "mV": 1e-3, "uV": 1e-6}
RESISTANCE_UNITS = {"ohm": 1.0, "kohm": 1e3, "Mohm": 1e6}
SCALE_EXPONENTS = {1.0: 0, 1e-3: -3, 1e-6: -6, 1e3: 3, 1e6: 6}
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
COUNT_KEYS = (
    "rows", "device_cycle_groups", "complete_pairs", "missing_pair_groups",
    "missing_hrs_groups", "missing_lrs_groups", "invalid_resistance_rows",
    "invalid_read_voltage_rows", "zero_read_voltage_pairs",
    "voltage_mismatch_pairs", "read_condition_mismatch_pairs", "unrepresentable_ratio_pairs", "valid_pairs",
    "excluded_groups", "ratios_below_one_pairs",
)


class InputError(ValueError):
    """The input cannot be interpreted under the explicit CSV contract."""


@dataclass(frozen=True)
class Observation:
    source_line: int
    read_condition: str
    resistance_ohm: float | None
    read_voltage_V: float | None
    exact_read_voltage: Decimal | None
    issues: tuple[str, ...]

    def public(self) -> dict:
        return {
            "source_line": self.source_line,
            "read_condition": self.read_condition,
            "resistance_ohm": self.resistance_ohm,
            "read_voltage_V": self.read_voltage_V,
            "read_voltage_input": str(self.exact_read_voltage) if self.exact_read_voltage is not None else None,
            "issues": list(self.issues),
        }


def _number(raw: str, scale: float, positive: bool) -> tuple[float | None, Decimal | None, str | None]:
    """Validate decimal notation and binary64 representability, never emit NaN."""
    if not raw:
        return None, None, "missing"
    if not NUMBER.fullmatch(raw):
        return None, None, "not_decimal_number"
    try:
        exact = Decimal(raw)
        parts = exact.as_tuple()
        normalized = float(Decimal((parts.sign, parts.digits, parts.exponent + SCALE_EXPONENTS[scale])))
    except (ValueError, InvalidOperation, OverflowError):
        return None, None, "not_decimal_number"
    if positive and exact <= 0:
        return None, None, "not_positive"
    if not math.isfinite(normalized) or (exact != 0 and normalized == 0):
        return None, None, "nonfinite_or_out_of_range"
    return normalized, exact, None


def _labels(hrs_label: str, lrs_label: str) -> tuple[str, str]:
    labels = (hrs_label, lrs_label)
    if any(label != label.strip() for label in labels):
        raise InputError("state labels must not have surrounding whitespace")
    if any(not label or any(ord(c) < 32 or ord(c) == 127 for c in label) for label in labels):
        raise InputError("HRS and LRS labels must be nonempty and contain no control characters")
    if labels[0] == labels[1]:
        raise InputError("HRS and LRS labels must be distinct (case-sensitive)")
    return labels


def _read(stream: TextIO, voltage_unit: str, resistance_unit: str,
          hrs_label: str, lrs_label: str) -> dict[tuple[str, str], dict[str, Observation]]:
    reader = csv.reader(stream, strict=True)
    groups: dict[tuple[str, str], dict[str, Observation]] = {}
    try:
        header = next(reader, None)
        if header is None:
            raise InputError("CSV is empty; expected a header and at least one observation")
        if len(header) != len(set(header)):
            raise InputError("CSV header contains duplicate column names")
        if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise InputError("CSV header must contain exactly: " + ",".join(COLUMNS))
        for values in reader:
            line = reader.line_num
            if len(values) != len(header):
                raise InputError(f"line {line}: expected {len(header)} fields, found {len(values)}")
            if any("\x00" in cell for cell in values):
                raise InputError(f"line {line}: NUL characters are not allowed")
            row = dict(zip(header, values))
            for key in ("device", "cycle", "state", "read_condition"):
                if not row[key] or any(ord(c) < 32 or ord(c) == 127 for c in row[key]):
                    raise InputError(f"line {line}: {key} must be nonempty and contain no control characters")
                if row[key] != row[key].strip():
                    raise InputError(f"line {line}: {key} must not have surrounding whitespace")
            if row["state"] not in (hrs_label, lrs_label):
                raise InputError(f"line {line}: state does not match either explicit state label")
            key = (row["device"], row["cycle"])
            states = groups.setdefault(key, {})
            if row["state"] in states:
                first = states[row["state"]].source_line
                raise InputError(f"line {line}: duplicate device/cycle/state observation (first at line {first})")
            resistance, _, r_error = _number(row["resistance"].strip(), RESISTANCE_UNITS[resistance_unit], True)
            voltage, exact_voltage, v_error = _number(row["read_voltage"].strip(), VOLTAGE_UNITS[voltage_unit], False)
            issues = []
            if r_error:
                issues.append("resistance:" + r_error)
            if v_error:
                issues.append("read_voltage:" + v_error)
            states[row["state"]] = Observation(line, row["read_condition"], resistance, voltage, exact_voltage, tuple(issues))
    except csv.Error as exc:
        raise InputError(f"malformed CSV near line {reader.line_num}") from exc
    if not groups:
        raise InputError("CSV has a header but no observations")
    return groups


def _summary(values: list[float]) -> dict:
    """Descriptive statistics only; no independence or population claim."""
    if not values:
        return {"count": 0, "minimum": None, "median": None, "maximum": None, "arithmetic_mean": None}
    ordered = sorted(values)
    count = len(ordered)
    midpoint = count // 2
    median = ordered[midpoint]
    if count % 2 == 0:
        low = ordered[midpoint - 1]
        median = low + (median - low) / 2
    maximum = ordered[-1]
    # Scaling avoids overflow in the sum of individually finite positive ratios.
    mean = maximum * (math.fsum(value / maximum for value in ordered) / count)
    return {"count": count, "minimum": ordered[0], "median": median,
            "maximum": maximum, "arithmetic_mean": mean}


def evaluate_stream(stream: TextIO, *, voltage_unit: str, resistance_unit: str,
                    hrs_label: str, lrs_label: str, source: str = "<stream>") -> dict:
    """Return diagnostics and descriptive per-device summaries for strict CSV."""
    if voltage_unit not in VOLTAGE_UNITS:
        raise InputError("unsupported voltage unit; choose V, mV, or uV")
    if resistance_unit not in RESISTANCE_UNITS:
        raise InputError("unsupported resistance unit; choose ohm, kohm, or Mohm")
    hrs_label, lrs_label = _labels(hrs_label, lrs_label)
    groups = _read(stream, voltage_unit, resistance_unit, hrs_label, lrs_label)
    totals = dict.fromkeys(COUNT_KEYS, 0)
    devices: dict[str, dict] = {}
    pairs = []
    for (device, cycle), states in sorted(groups.items()):
        record = devices.setdefault(device, {"counts": dict.fromkeys(COUNT_KEYS, 0), "ratios": [], "by_voltage": {}})
        counts = record["counts"]
        counts["device_cycle_groups"] += 1
        counts["rows"] += len(states)
        counts["invalid_resistance_rows"] += sum(o.resistance_ohm is None for o in states.values())
        counts["invalid_read_voltage_rows"] += sum(o.read_voltage_V is None for o in states.values())
        hrs, lrs = states.get(hrs_label), states.get(lrs_label)
        reasons = []
        ratio = None
        common_voltage = None
        if hrs is None or lrs is None:
            counts["missing_pair_groups"] += 1
            if hrs is None:
                counts["missing_hrs_groups"] += 1
                reasons.append("missing_hrs")
            if lrs is None:
                counts["missing_lrs_groups"] += 1
                reasons.append("missing_lrs")
        else:
            counts["complete_pairs"] += 1
        if any(o.resistance_ohm is None for o in states.values()):
            reasons.append("invalid_resistance")
        if any(o.read_voltage_V is None for o in states.values()):
            reasons.append("invalid_read_voltage")
        if hrs is not None and lrs is not None:
            if hrs.read_condition != lrs.read_condition:
                counts["read_condition_mismatch_pairs"] += 1
                reasons.append("read_condition_mismatch")
            if hrs.read_voltage_V == 0 or lrs.read_voltage_V == 0:
                counts["zero_read_voltage_pairs"] += 1
                reasons.append("zero_read_voltage")
            if hrs.exact_read_voltage is not None and lrs.exact_read_voltage is not None:
                if hrs.exact_read_voltage != lrs.exact_read_voltage:
                    counts["voltage_mismatch_pairs"] += 1
                    reasons.append("read_voltage_mismatch")
                elif hrs.read_voltage_V != 0:
                    common_voltage = hrs.read_voltage_V
            if not reasons:
                ratio = hrs.resistance_ohm / lrs.resistance_ohm
                if not math.isfinite(ratio) or ratio <= 0:
                    ratio = None
                    counts["unrepresentable_ratio_pairs"] += 1
                    reasons.append("ratio_out_of_range")
        valid = not reasons
        if valid:
            counts["valid_pairs"] += 1
            counts["ratios_below_one_pairs"] += int(ratio < 1)
            record["ratios"].append(ratio)
            # Exact decimal values remain distinct even when binary64 rounds them alike.
            voltage_key = (hrs.read_condition, hrs.exact_read_voltage)
            bucket = record["by_voltage"].setdefault(voltage_key, {"voltage_V": common_voltage, "ratios": []})
            bucket["ratios"].append(ratio)
        else:
            counts["excluded_groups"] += 1
        pairs.append({"device": device, "cycle": cycle, "status": "valid" if valid else "excluded",
                      "reasons": reasons, "hrs": hrs.public() if hrs else None,
                      "lrs": lrs.public() if lrs else None, "common_read_voltage_V": common_voltage,
                      "resistance_ratio": ratio, "ratio_below_one": ratio < 1 if ratio is not None else None})
    device_output = []
    for device, record in sorted(devices.items()):
        for key in COUNT_KEYS:
            totals[key] += record["counts"][key]
        voltage_summaries = []
        for (condition, exact), bucket in sorted(record["by_voltage"].items()):
            voltage_summaries.append({"read_condition": condition, "read_voltage_input": str(exact), "read_voltage_V": bucket["voltage_V"],
                                      "resistance_ratio_summary": _summary(bucket["ratios"])})
        device_output.append({"device": device, "counts": record["counts"],
                              "resistance_ratio_summary": None if len(voltage_summaries) > 1 else _summary(record["ratios"]),
                              "by_read_condition_and_voltage": voltage_summaries,
                              "mixed_read_conditions_or_voltages": len(voltage_summaries) > 1})
    totals["devices"] = len(devices)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "rram-window",
        "input": {"source": source, "voltage_unit": voltage_unit, "resistance_unit": resistance_unit,
                  "hrs_label": hrs_label, "lrs_label": lrs_label},
        "method": {"ratio": "R_HRS / R_LRS", "read_voltage_match": "exact decimal equality under common SI scaling, signed, nonzero",
                   "read_condition_match": "exact user-declared condition label equality; actual measurement protocol is not verified",
                   "normalized_units": {"read_voltage": "V", "resistance": "ohm", "resistance_ratio": "dimensionless"},
                   "cycle_interpretation": "repeated observations within a device, not independent samples",
                   "summary_interpretation": "descriptive only; consult by_read_condition_and_voltage before combining conditions"},
        "counts": totals,
        "per_device": device_output,
        "pairs": pairs,
    }


def evaluate_csv(path: str | Path, **kwargs) -> dict:
    """Read a UTF-8 CSV (optional BOM) and evaluate it without modifying input."""
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            return evaluate_stream(stream, source=str(path), **kwargs)
    except UnicodeError as exc:
        raise InputError("input must be UTF-8 text") from exc
    except OSError as exc:
        raise InputError("cannot read input file") from exc


class SafeParser(argparse.ArgumentParser):
    """Avoid repeating paths, raw arguments, or measurement identifiers in errors."""

    def error(self, message: str) -> None:
        self.exit(2, "rram-window: error: invalid command-line arguments; see --help\n")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(description="Describe explicit HRS/LRS RRAM resistance pairs with matching declared conditions and common nonzero read voltage. No state or mechanism inference.")
    parser.add_argument("input", type=Path, help="UTF-8 CSV with device,cycle,state,read_condition,read_voltage,resistance")
    parser.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS, help="unit for every read_voltage value; never inferred")
    parser.add_argument("--resistance-unit", required=True, choices=RESISTANCE_UNITS, help="unit for every resistance value; case-sensitive")
    parser.add_argument("--hrs-label", required=True, help="exact user-assigned high-resistance state label")
    parser.add_argument("--lrs-label", required=True, help="exact user-assigned low-resistance state label")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="create a new JSON file exclusively; existing paths are never overwritten")
    destination.add_argument("--stdout", action="store_true", help="explicitly print JSON including device IDs and measurements to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_csv(args.input, voltage_unit=args.voltage_unit, resistance_unit=args.resistance_unit,
                              hrs_label=args.hrs_label, lrs_label=args.lrs_label)
        payload = json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(payload)
        else:
            try:
                descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError as exc:
                raise InputError("cannot create output file; use a new writable path") from exc
            # Never unlink by pathname after creation: another process could
            # replace it during a write failure. A partial file is left for review.
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(payload)
    except InputError as exc:
        print(f"rram-window: error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("rram-window: error: unable to serialize or write report", file=sys.stderr)
        return 2
    return 1 if result["counts"]["excluded_groups"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
