#!/usr/bin/env python3
"""Extract a declared constant-current VGS criterion from separate transfer sweeps.

Standard library only. No instrument access, network, smoothing, extrapolation,
automatic geometry normalization, branch merging or physical-threshold inference.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, DecimalException
from fractions import Fraction
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

VERSION = "0.1.0"
GROUP_COLUMNS = ("lot", "wafer", "device", "condition", "replicate", "branch")
COLUMNS = (*GROUP_COLUMNS, "temperature_K", "drain_voltage", "body_voltage",
           "gate_voltage", "drain_current")
VOLTAGE_UNITS = {"V": "1", "mV": "0.001", "uV": "0.000001"}
CURRENT_UNITS = {"A": "1", "mA": "0.001", "uA": "0.000001", "nA": "0.000000001",
                 "pA": "0.000000000001"}
CURRENT_POLARITIES = ("positive", "negative")
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_GROUPS = 1000
MAX_GROUP_ROWS = 1000
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")
MIN_NUMBER = Fraction(1, 10 ** 150)
MAX_NUMBER = Fraction(10 ** 150)
SOURCE_URL = "https://download.tek.com/manual/ACS-907-01_F_Nov_2017_Prog_ACS-907-01F.pdf"
ASSUMPTIONS = [
    "Gate, drain and body voltages are signed VGS, VDS and VBS, each referenced to source; drain current uses the explicitly declared current-sign convention, not an inferred transistor type.",
    "Each lot/wafer/device/condition/replicate/branch key denotes one acquisition-ordered strictly monotonic VGS sweep at fixed nonzero VDS, fixed VBS, positive temperature in kelvin and one fixed protocol/state. Separate forward and reverse branches are never averaged.",
    "The signed nonzero drain-current criterion is supplied by the user as unnormalized terminal current in amperes after unit conversion. No W/L, area or width normalization is performed and no universal criterion is chosen.",
    "This is a criterion-defined VGS, often called constant-current threshold voltage, not a universal physical threshold, an intrinsic material property, a turn-on guarantee or evidence of a microscopic mechanism.",
    "Interpolation is linear in signed drain current versus VGS only between the reported adjacent measured samples, never logarithmic and never extrapolated. Strict VGS order and non-strict monotonic current are required; no smoothing, sorting, averaging or outlier removal is applied.",
    "The software cannot establish device type, bias regime, compliance, noise floor, settling, temperature accuracy, contact effects or protocol stability. A unique crossing is not metrology validation, and bracket width is not measurement uncertainty.",
    "Exact decimal/rational arithmetic is used until JSON binary64 conversion; source input must be retained. Replicates and branches are not assumed to be independent devices. No uncertainty propagation or population statistics are provided.",
]


class InputError(ValueError):
    """Safe diagnostic with no raw identifiers, input values or file paths."""


def finite_float(value):
    try:
        result = float(value)
    except (ValueError, OverflowError, DecimalException):
        raise InputError("derived numeric value is outside the supported finite range") from None
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise InputError("derived numeric value is outside the supported finite range")
    return result


def number(text, factor):
    """Bound syntax and decimal size before constructing exact rational values."""
    value = text.strip()
    if len(value) > 64 or not NUMBER.fullmatch(value):
        raise InputError("invalid numeric field")
    original = Decimal(value)
    if len(original.as_tuple().digits) > 32 or abs(original.as_tuple().exponent) > 200:
        raise InputError("numeric field exceeds decimal precision or exponent limits")
    scaled = Fraction(original) * Fraction(factor)
    if scaled and not MIN_NUMBER <= abs(scaled) <= MAX_NUMBER:
        raise InputError("nonzero SI input magnitude must be between 1e-150 and 1e150")
    return scaled


def identifier(value):
    if (not value or value != value.strip() or len(value) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise InputError("identifiers must be nonempty, unpadded and control-free")
    return value


def load_csv(path, voltage_unit, current_unit):
    if voltage_unit not in VOLTAGE_UNITS or current_unit not in CURRENT_UNITS:
        raise InputError("unsupported input unit")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InputError("input must be a regular CSV file")
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputError("CSV exceeds the input byte limit")
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, None)
        if header is None or len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise InputError("CSV header must contain exactly the documented transfer-sweep columns")
        groups = {}
        rows = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise InputError("CSV row width does not match the header")
            record = dict(zip(header, row))
            key = tuple(identifier(record[name]) for name in GROUP_COLUMNS)
            point = {"source_row": reader.line_num,
                     "temperature_K": number(record["temperature_K"], "1"),
                     "vds": number(record["drain_voltage"], VOLTAGE_UNITS[voltage_unit]),
                     "vbs": number(record["body_voltage"], VOLTAGE_UNITS[voltage_unit]),
                     "vgs": number(record["gate_voltage"], VOLTAGE_UNITS[voltage_unit]),
                     "id": number(record["drain_current"], CURRENT_UNITS[current_unit])}
            rows += 1
            points = groups.setdefault(key, [])
            points.append(point)
            if rows > MAX_ROWS or len(groups) > MAX_GROUPS or len(points) > MAX_GROUP_ROWS:
                raise InputError("CSV exceeds row or group limits")
        if not rows:
            raise InputError("CSV contains no measurement rows")
    except (OSError, UnicodeError, csv.Error):
        raise InputError("unable to read a valid UTF-8 CSV input") from None
    return groups, rows, hashlib.sha256(raw).hexdigest()


def observation(point):
    return {"source_row": point["source_row"], "gate_source_voltage_V": finite_float(point["vgs"]),
            "drain_current_A": finite_float(point["id"]),
            "drain_source_voltage_V": finite_float(point["vds"]),
            "body_source_voltage_V": finite_float(point["vbs"]),
            "temperature_K": finite_float(point["temperature_K"])}


def extract_group(key, points, criterion, current_polarity, max_span=None):
    result = dict(zip(GROUP_COLUMNS, key))
    result.update({"status": "rejected", "sample_count": len(points), "extraction": None,
                   "observations": [observation(p) for p in points],
                   "warnings": ["criterion_defined_value_not_universal_physical_threshold",
                                "branches_and_replicates_not_independent_devices"]})

    def reject(reason):
        result["reason"] = reason
        return result

    if len(points) < 2:
        return reject("fewer_than_two_samples")
    if any(p["temperature_K"] <= 0 for p in points):
        return reject("nonpositive_temperature")
    if any(p["vds"] == 0 for p in points):
        return reject("zero_drain_bias")
    for field in ("temperature_K", "vds", "vbs"):
        if len({p[field] for p in points}) != 1:
            return reject("inconsistent_temperature_or_bias")
    result["conditions"] = {"temperature_K": finite_float(points[0]["temperature_K"]),
                            "drain_source_voltage_V": finite_float(points[0]["vds"]),
                            "body_source_voltage_V": finite_float(points[0]["vbs"])}
    sign = 1 if current_polarity == "positive" else -1
    if any(p["id"] * sign < 0 for p in points):
        return reject("current_sign_conflicts_with_declared_polarity")
    voltages = [p["vgs"] for p in points]
    currents = [p["id"] for p in points]
    if len(set(voltages)) != len(voltages):
        return reject("duplicate_gate_voltage")
    ascending = all(b > a for a, b in zip(voltages, voltages[1:]))
    descending = all(b < a for a, b in zip(voltages, voltages[1:]))
    if not (ascending or descending):
        return reject("nonmonotonic_gate_voltage")
    result["acquisition_direction"] = "increasing_vgs" if ascending else "decreasing_vgs"
    result["gate_voltage_range_V"] = [finite_float(min(voltages)), finite_float(max(voltages))]
    result["drain_current_range_A"] = [finite_float(min(currents)), finite_float(max(currents))]
    for values, name in ((voltages, "gate_voltage"), (currents, "drain_current")):
        if len(set(values)) != len({finite_float(v) for v in values}):
            result["warnings"].append("binary64_" + name + "_rounding_merges_distinct_inputs")
    # Count distinct exact hits and strict interior crossings. An exact hit is
    # not counted twice as two neighboring segments; a plateau is not unique.
    exact = [[p] for p in points if p["id"] == criterion]
    interiors = [[a, b] for a, b in zip(points, points[1:])
                 if min(a["id"], b["id"]) < criterion < max(a["id"], b["id"])]
    if len(exact) + len(interiors) > 1:
        return reject("nonunique_current_crossing")
    if not exact and not interiors:
        return reject("criterion_not_bracketed")
    rising = all(b >= a for a, b in zip(currents, currents[1:]))
    falling = all(b <= a for a, b in zip(currents, currents[1:]))
    if not (rising or falling):
        return reject("nonmonotonic_drain_current")
    result["current_direction"] = "increasing_or_flat" if rising else "decreasing_or_flat"
    source = (exact or interiors)[0]
    try:
        if exact:
            threshold = source[0]["vgs"]
            weight = Fraction(0)
            span = Fraction(0)
            method = "measured_exact_criterion"
        else:
            a, b = source
            span = abs(b["vgs"] - a["vgs"])
            if max_span is not None and span > max_span:
                return reject("bracket_span_exceeds_limit")
            weight = (criterion - a["id"]) / (b["id"] - a["id"])
            threshold = a["vgs"] + weight * (b["vgs"] - a["vgs"])
            method = "linear_current_interpolation"
        # A strict exact crossing guarantees 0 < weight < 1. Its diagnostic
        # binary64 value may round to either endpoint, but must not determine
        # whether the independently converted exact threshold is representable.
        extraction = {"method": method, "gate_source_voltage_at_criterion_V": finite_float(threshold),
                      "signed_current_criterion_A": finite_float(criterion),
                      "bracket_span_V": finite_float(span),
                      "fraction_from_first_to_second_sample": float(weight) if not exact else None,
                      "source_rows": [p["source_row"] for p in source],
                      "bracket_gate_source_voltage_V": [finite_float(p["vgs"]) for p in source],
                      "bracket_drain_current_A": [finite_float(p["id"]) for p in source]}
    except InputError:
        return reject("derived_numeric_range_error")
    if not exact:
        result["warnings"].append("local_linear_interpolation_is_not_measurement_uncertainty")
        if extraction["fraction_from_first_to_second_sample"] in (0.0, 1.0):
            result["warnings"].append("binary64_rounding_hides_interior_interpolation_fraction")
        if finite_float(criterion) in [finite_float(p["id"]) for p in source]:
            result["warnings"].append("binary64_rounding_hides_criterion_bracket_separation")
    result.update({"status": "ok", "extraction": extraction})
    return result


def evaluate(path, voltage_unit, current_unit, criterion_current, criterion_current_unit,
             current_polarity, assumptions_accepted=False, branch=None, max_bracket_span=None):
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of transfer-sweep assumptions is required")
    if (voltage_unit not in VOLTAGE_UNITS or current_unit not in CURRENT_UNITS
            or criterion_current_unit not in CURRENT_UNITS):
        raise InputError("unsupported input or criterion unit")
    if current_polarity not in CURRENT_POLARITIES:
        raise InputError("current polarity must be explicitly positive or negative")
    criterion = number(criterion_current, CURRENT_UNITS[criterion_current_unit])
    sign = 1 if current_polarity == "positive" else -1
    if criterion * sign <= 0:
        raise InputError("current criterion must be nonzero and match the declared current polarity")
    span = None
    if max_bracket_span is not None:
        span = number(max_bracket_span, VOLTAGE_UNITS[voltage_unit])
        if span <= 0:
            raise InputError("maximum bracket span must be positive")
    if branch is not None:
        identifier(branch)
    groups, row_count, digest = load_csv(path, voltage_unit, current_unit)
    selected = [(key, points) for key, points in groups.items() if branch is None or key[-1] == branch]
    if not selected:
        raise InputError("branch filter matched no measurement group")
    results = [extract_group(key, points, criterion, current_polarity, span) for key, points in selected]
    rejected = sum(item["status"] == "rejected" for item in results)
    reasons = {}
    for item in results:
        if item["status"] == "rejected":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    return {
        "schema_version": 1, "tool": "vth-constant-current", "version": VERSION,
        "input_units": {"gate_drain_body_voltage": voltage_unit, "drain_current": current_unit,
                        "temperature": "K", "criterion_current": criterion_current_unit},
        "provenance": {"input_sha256": digest, "source_row_convention": "CSV physical line at end of record, header included",
                       "group_columns": list(GROUP_COLUMNS), "reference": SOURCE_URL},
        "parameters": {"transfer_assumptions_declared": True, "current_polarity": current_polarity,
                       "criterion_current_input": criterion_current.strip(),
                       "signed_current_criterion_A": finite_float(criterion), "current_normalization": "none",
                       "interpolation": "linear_current", "branch_filter": branch,
                       "max_bracket_span_input": max_bracket_span.strip() if max_bracket_span is not None else None,
                       "max_bracket_span_V": finite_float(span) if span is not None else None},
        "counts": {"input_rows": row_count, "input_groups": len(groups), "selected_groups": len(results),
                   "excluded_by_branch_filter": len(groups) - len(results),
                   "extracted_groups": len(results) - rejected, "rejected_groups": rejected,
                   "rejected_by_reason": reasons},
        "assumptions_and_limits": ASSUMPTIONS, "results": results,
    }


def write_report(report, output=None):
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if output is None:
        sys.stdout.write(payload)
        return
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "error: invalid command-line arguments; use --help for required schema and options\n")


def parser():
    result = SafeParser(description="Extract signed VGS at a declared signed drain-current criterion from separate monotonic transfer sweeps; no extrapolation or geometry normalization.")
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS,
                        help="Unit shared by gate_voltage VGS, drain_voltage VDS, body_voltage VBS and max bracket span")
    result.add_argument("--current-unit", required=True, choices=CURRENT_UNITS)
    result.add_argument("--criterion-current", required=True,
                        help="Signed nonzero unnormalized drain current; use --criterion-current=-1 for negatives")
    result.add_argument("--criterion-current-unit", required=True, choices=CURRENT_UNITS)
    result.add_argument("--current-polarity", required=True, choices=CURRENT_POLARITIES,
                        help="Declared drain-current sign, not an inferred transistor type")
    result.add_argument("--accept-transfer-assumptions", required=True, action="store_true",
                        help="Declare source-referenced voltages and separate fixed-condition/protocol sweeps; see README")
    result.add_argument("--branch", help="Exact declared branch label; otherwise process every branch separately")
    result.add_argument("--max-bracket-span", help="Optional positive interpolation bracket bound in --voltage-unit")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new owner-only JSON report; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print identifiers, observations and extracted values")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.voltage_unit, args.current_unit, args.criterion_current,
                          args.criterion_current_unit, args.current_polarity, args.accept_transfer_assumptions,
                          args.branch, args.max_bracket_span)
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
