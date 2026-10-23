#!/usr/bin/env python3
"""Conventional linear TLM fits with explicit geometry and measurement groups.

Only standard-library code and user-supplied CSV; no network or instrument access.
The declared model is R_total = R_sheet * spacing / width + 2 * R_contact.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, DecimalException, localcontext
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
GROUP_COLUMNS = ("lot", "wafer", "structure", "condition", "replicate")
COLUMNS = (*GROUP_COLUMNS, "pair", "spacing", "width", "resistance")
LENGTH_UNITS = {"m": "1", "mm": "0.001", "um": "0.000001", "nm": "0.000000001"}
RESISTANCE_UNITS = {"ohm": "1", "kohm": "1000", "Mohm": "1000000"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_GROUPS = 1000
MAX_GROUP_ROWS = 1000
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")
MIN_NUMBER = Fraction(1, 10 ** 150)
MAX_NUMBER = Fraction(10 ** 150)
SOURCE_URL = "https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=924311"
ASSUMPTIONS = [
    "Declared conventional rectangular linear TLM: R_total = R_sheet * spacing / width + 2 * R_contact. Spacing is the positive edge-to-edge gap, not contact pitch; width is transverse to channel current.",
    "Each group has a uniform channel sheet resistance, constant width, equal Ohmic contacts with the same geometry, and one fixed bias, temperature, state and measurement protocol. The software cannot verify these declarations.",
    "Lead, probe, metal and other series resistance are negligible or removed before input. Otherwise intercept/2 is an apparent contribution, not an isolated contact resistance.",
    "Every row is equally weighted in ordinary least squares, treating gap as exact. Rows may share contacts; repeated gaps, pairs, replicates or structures do not establish statistically independent devices. No confidence intervals or population aggregation are provided.",
    "The zero-spacing intercept is extrapolated. Positive coefficients or high R-squared do not establish the model, Ohmic conduction, measurement accuracy, or contact quality.",
    "Only channel sheet resistance in ohm/square and per-contact resistance in ohm are calculated. No specific contact resistivity, transfer length, under-contact sheet resistance or mechanism is inferred.",
    "Decimal inputs and regression intermediates are exact rational values; JSON numbers are rounded binary64. Nonzero results that cannot be represented as finite nonzero binary64 are rejected, not clipped.",
]


class InputError(ValueError):
    """Safe diagnostic containing no user values, identifiers or file paths."""


def finite_float(value):
    try:
        result = float(value)
    except (ValueError, OverflowError, DecimalException):
        raise InputError("derived numeric value is outside the supported finite range") from None
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise InputError("derived numeric value is outside the supported finite range")
    return result


def number(text, factor):
    """Parse bounded decimal syntax before constructing a rational number."""
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


def load_csv(path, length_unit, resistance_unit):
    if length_unit not in LENGTH_UNITS or resistance_unit not in RESISTANCE_UNITS:
        raise InputError("unsupported input unit")
    try:
        # Nonblocking open avoids hanging on a FIFO. Only regular input files qualify.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InputError("input must be a regular CSV file")
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputError("CSV exceeds the input byte limit")
        content = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content, newline=""), strict=True)
        header = next(reader, None)
        if header is None or len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise InputError("CSV header must contain exactly lot,wafer,structure,condition,replicate,pair,spacing,width,resistance")
        groups = {}
        rows = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise InputError("CSV row width does not match the header")
            record = dict(zip(header, row))
            key = tuple(identifier(record[name]) for name in GROUP_COLUMNS)
            point = {"pair": identifier(record["pair"]), "source_row": reader.line_num,
                     "spacing": number(record["spacing"], LENGTH_UNITS[length_unit]),
                     "width": number(record["width"], LENGTH_UNITS[length_unit]),
                     "resistance": number(record["resistance"], RESISTANCE_UNITS[resistance_unit])}
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


def root_fraction(value):
    """Compute sqrt without first underflowing/overflowing a squared quantity."""
    with localcontext() as context:
        context.prec = 50
        result = (Decimal(value.numerator) / Decimal(value.denominator)).sqrt()
    return finite_float(result)


def extract_group(key, points):
    result = dict(zip(GROUP_COLUMNS, key))
    result.update({"status": "rejected", "sample_count": len(points),
                   "distinct_spacing_count": len({p["spacing"] for p in points}),
                   "source_rows": [p["source_row"] for p in points],
                   "fit": None, "physical_estimates": None, "warnings": []})
    # Retain normalized observations even when this group cannot be fitted.
    result["observations"] = [{"pair": p["pair"], "source_row": p["source_row"],
                               "spacing_m": finite_float(p["spacing"]),
                               "width_m": finite_float(p["width"]),
                               "resistance_ohm": finite_float(p["resistance"])} for p in points]
    if len({p["pair"] for p in points}) != len(points):
        result["reason"] = "duplicate_pair_id"
        return result
    if any(p["spacing"] <= 0 or p["width"] <= 0 for p in points):
        result["reason"] = "nonpositive_geometry"
        return result
    if any(p["resistance"] <= 0 for p in points):
        result["reason"] = "nonpositive_measured_resistance"
        return result
    if len({p["width"] for p in points}) != 1:
        result["reason"] = "inconsistent_width"
        return result
    if result["distinct_spacing_count"] < 3:
        result["reason"] = "fewer_than_three_distinct_spacings"
        return result
    warnings = result["warnings"]
    warnings.extend(["zero_spacing_intercept_is_extrapolated", "rows_not_assumed_independent"])
    if result["distinct_spacing_count"] != len(points):
        warnings.append("repeated_spacing_equal_row_weighting")
    x = [p["spacing"] for p in points]
    y = [p["resistance"] for p in points]
    n = len(points)
    if len({finite_float(v) for v in x}) < len(set(x)):
        warnings.append("binary64_spacing_rounding_merges_distinct_inputs")
    # Exact rational normal equations avoid catastrophic floating-point cancellation.
    sx, sy = sum(x), sum(y)
    sxx = sum(v * v for v in x)
    sxy = sum(a * b for a, b in zip(x, y))
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n
    predicted = [slope * value + intercept for value in x]
    residuals = [observed - fitted for observed, fitted in zip(y, predicted)]
    sse = sum(value * value for value in residuals)
    syy = sum((value - sy / n) ** 2 for value in y)
    extrapolation = min(x) / (max(x) - min(x))
    if extrapolation >= 10:
        warnings.append("large_zero_spacing_extrapolation")
    if syy == 0:
        warnings.append("constant_resistance_r_squared_undefined")
    if intercept == 0:
        warnings.append("zero_intercept_not_proof_of_zero_contact_resistance")
    try:
        result["fit"] = {
            "method": "unweighted_ordinary_least_squares",
            "slope_ohm_per_m": finite_float(slope),
            "intercept_ohm": finite_float(intercept),
            "r_squared": finite_float(1 - sse / syy) if syy else None,
            "rmse_ohm": root_fraction(sse / n),
            "max_abs_residual_ohm": finite_float(max(abs(v) for v in residuals)),
            "spacing_range_m": [finite_float(min(x)), finite_float(max(x))],
            "width_m": finite_float(points[0]["width"]),
            "zero_spacing_extrapolation_ratio": finite_float(extrapolation),
        }
        for observation, fitted, residual in zip(result["observations"], predicted, residuals):
            observation["fitted_resistance_ohm"] = finite_float(fitted)
            observation["residual_ohm"] = finite_float(residual)
        issues = []
        if slope <= 0:
            issues.append("nonpositive_slope")
        if intercept < 0:
            issues.append("negative_intercept")
        if issues:
            result["reason"] = "nonphysical_fit"
            result["fit_failures"] = issues
            return result
        result["physical_estimates"] = {
            "sheet_resistance_ohm_per_square": finite_float(slope * points[0]["width"]),
            "per_contact_resistance_ohm": finite_float(intercept / 2),
        }
    except InputError:
        result["reason"] = "derived_numeric_range_error"
        return result
    result["status"] = "ok"
    return result


def evaluate(path, length_unit, resistance_unit, assumptions_accepted=False):
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of linear TLM assumptions is required")
    groups, row_count, digest = load_csv(path, length_unit, resistance_unit)
    results = [extract_group(key, points) for key, points in groups.items()]
    # Replicate labels segregate fits, but cannot establish independent samples.
    structures = {}
    for key in groups:
        structures.setdefault(key[:4], set()).add(key[4])
    for result in results:
        if len(structures[tuple(result[name] for name in GROUP_COLUMNS[:4])]) > 1:
            result["warnings"].append("replicate_groups_not_independent_devices")
    rejected = sum(item["status"] == "rejected" for item in results)
    reasons = {}
    for item in results:
        if item["status"] == "rejected":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    return {
        "schema_version": 1, "tool": "tlm-extract", "version": VERSION,
        "input_units": {"spacing_and_width": length_unit, "resistance": resistance_unit},
        "provenance": {"input_sha256": digest, "source_row_convention": "CSV physical line at end of record, header included",
                       "group_columns": list(GROUP_COLUMNS), "reference": SOURCE_URL},
        "parameters": {"linear_tlm_assumptions_declared": True,
                       "minimum_distinct_spacings": 3, "row_weighting": "equal"},
        "counts": {"input_rows": row_count, "input_groups": len(groups),
                   "extracted_groups": len(results) - rejected, "rejected_groups": rejected,
                   "rejected_by_reason": reasons},
        "assumptions_and_limits": ASSUMPTIONS,
        "results": results,
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
    result = SafeParser(description="Fit conventional linear TLM resistance vs edge-to-edge gap; equal contacts and uniform geometry must be declared, not inferred.")
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--length-unit", required=True, choices=LENGTH_UNITS, help="Unit shared by spacing and width")
    result.add_argument("--resistance-unit", required=True, choices=RESISTANCE_UNITS)
    result.add_argument("--accept-linear-tlm-assumptions", required=True, action="store_true",
                        help="Declare equal Ohmic contacts, uniform channel/width, fixed conditions and negligible/removed parasitics; see README")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new owner-only JSON report; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print identifiers, measurements and fitted values")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.length_unit, args.resistance_unit, args.accept_linear_tlm_assumptions)
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
