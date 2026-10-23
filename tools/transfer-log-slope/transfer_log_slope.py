#!/usr/bin/env python3
"""Describe log10 of declared-polarity current versus VGS in a user voltage window.

Standard library only. No inferred subthreshold region, window search, outlier
removal, branch merging, sign repair, extrapolation, or physical certification.
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
LOG_PRECISION = 80
SOURCES = [
    "https://download.tek.com/manual/4200-904-01E.pdf",
    "https://download.tek.com/manual/ACS2600RTM-900-01(B-Dec2012)(User).pdf",
]
ASSUMPTIONS = [
    "VGS, VDS and VBS are signed and source referenced. The declared current polarity defines s=+1 or -1, not transistor type. Only strictly positive s*ID can be logged; no absolute-value sign repair is performed.",
    "Each lot/wafer/device/condition/replicate/branch is one acquisition-ordered strictly monotonic VGS sweep at fixed nonzero VDS, fixed VBS, positive fixed temperature in kelvin and one documented fixed protocol/state. Branches and replicates remain separate and are not independent devices.",
    "The user supplies one inclusive VGS window applied identically to every branch. All measured samples in it are fitted with equal row weights and at least three distinct VGS points are required. No current filtering, endpoint interpolation, extrapolation, best-window search, smoothing or outlier removal is performed.",
    "The response is y=log10(s*ID_A / 1 A), fitted as y=a+b*VGS_V. b is signed decades/V; 1/b is signed V/decade, 1000/b signed mV/decade. Its separately labeled magnitude is not a certified subthreshold swing. A zero slope has no reciprocal.",
    "This is a descriptive unweighted log-current fit with VGS treated as exact, not a local derivative, a reverse regression, a physical subthreshold classification or an intrinsic device property. The user must independently justify the window, bias regime and measurement protocol before any subthreshold interpretation.",
    "Residuals are observed minus fitted log10 current in decades. RMSE uses N, not N-2. Residuals and R-squared are diagnostics, not uncertainty, confidence intervals or proof of linearity; tiny residuals may come from finite-precision logarithms.",
    "No noise floor, leakage/background, compliance, self-heating, settling, current-density or geometry correction is inferred. Zero or sign-conflicting selected current rejects the whole group; unselected finite currents are retained and not logged. Nonmonotonic current is retained and warned, not trimmed.",
    "Decimal/rational parsing and window selection are exact. Base-10 logarithms use 80 significant decimal digits; regression on those rounded logarithms is exact rational arithmetic. Reports use binary64 and may round source values. Keep original input; numeric-range failure rejects the fit rather than clipping a value.",
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
                     "id": number(record["drain_current"], CURRENT_UNITS[current_unit]),
                     "input_numeric_text": {name: record[name] for name in COLUMNS[6:]}}
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


def observation(point, lower, upper, sign):
    selection = ("below_user_window" if point["vgs"] < lower else
                 "above_user_window" if point["vgs"] > upper else "inside_user_window")
    current_status = ("zero_current" if point["id"] == 0 else
                      "polarity_conflict" if point["id"] * sign < 0 else "positive_declared_polarity_current")
    return {"source_row": point["source_row"],
            "input_numeric_text": point["input_numeric_text"],
            "gate_source_voltage_V": finite_float(point["vgs"]),
            "drain_current_A": finite_float(point["id"]),
            "drain_source_voltage_V": finite_float(point["vds"]),
            "body_source_voltage_V": finite_float(point["vbs"]),
            "temperature_K": finite_float(point["temperature_K"]),
            "window_selection": selection, "current_log_domain": current_status,
            "used_in_fit": False}


def log10_fraction(value):
    """Return the rounded base-10 logarithm as an exact rational number."""
    with localcontext() as context:
        context.prec = LOG_PRECISION
        value_decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return Fraction(value_decimal.log10())


def root_fraction(value):
    with localcontext() as context:
        context.prec = LOG_PRECISION
        return finite_float((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())


def extract_group(key, points, lower, upper, current_polarity):
    sign = 1 if current_polarity == "positive" else -1
    selected = [p for p in points if lower <= p["vgs"] <= upper]
    result = dict(zip(GROUP_COLUMNS, key))
    result.update({"status": "rejected", "sample_count": len(points),
                   "source_rows": [p["source_row"] for p in points],
                   "selected_sample_count": len(selected),
                   "selected_source_rows": [p["source_row"] for p in selected],
                   "outside_window_source_rows": [p["source_row"] for p in points if not lower <= p["vgs"] <= upper],
                   "observations": [observation(p, lower, upper, sign) for p in points],
                   "fit": None, "reciprocal_slope": None,
                   "warnings": ["user_window_not_certified_subthreshold",
                                "descriptive_log_fit_not_physical_model_validation",
                                "branches_and_replicates_not_independent_devices"]})
    warnings = result["warnings"]

    def reject(reason):
        result["reason"] = reason
        return result

    if any(p["temperature_K"] <= 0 for p in points):
        return reject("nonpositive_temperature")
    if any(p["vds"] == 0 for p in points):
        return reject("zero_drain_bias")
    if any(len({p[field] for p in points}) != 1 for field in ("temperature_K", "vds", "vbs")):
        return reject("inconsistent_temperature_or_bias")
    result["conditions"] = {"temperature_K": finite_float(points[0]["temperature_K"]),
                            "drain_source_voltage_V": finite_float(points[0]["vds"]),
                            "body_source_voltage_V": finite_float(points[0]["vbs"])}
    voltages = [p["vgs"] for p in points]
    if len(set(voltages)) != len(voltages):
        return reject("duplicate_gate_voltage")
    if len(points) < 3:
        return reject("fewer_than_three_branch_samples")
    ascending = all(b > a for a, b in zip(voltages, voltages[1:]))
    descending = all(b < a for a, b in zip(voltages, voltages[1:]))
    if not (ascending or descending):
        return reject("nonmonotonic_gate_voltage")
    result["acquisition_direction"] = "increasing_vgs" if ascending else "decreasing_vgs"
    result["branch_gate_voltage_range_V"] = [finite_float(min(voltages)), finite_float(max(voltages))]
    if lower < min(voltages) or upper > max(voltages):
        warnings.append("requested_window_extends_beyond_measured_branch_no_extrapolation")
    if any(p["id"] * sign <= 0 for p in points if not lower <= p["vgs"] <= upper):
        warnings.append("outside_window_nonloggable_current_retained_not_fitted")
    if len(selected) < 3:
        return reject("fewer_than_three_samples_in_user_window")
    if any(p["id"] == 0 for p in selected):
        return reject("zero_current_in_user_window")
    if any(p["id"] * sign < 0 for p in selected):
        return reject("current_sign_conflicts_with_declared_polarity_in_user_window")
    x = [p["vgs"] for p in selected]
    current = [sign * p["id"] for p in selected]
    for values, name in ((voltages, "gate_voltage"), ([p["id"] for p in points], "drain_current")):
        if len(set(values)) != len({finite_float(v) for v in values}):
            warnings.append("binary64_" + name + "_rounding_merges_distinct_inputs")
    if not (all(b >= a for a, b in zip(current, current[1:])) or
            all(b <= a for a, b in zip(current, current[1:]))):
        warnings.append("nonmonotonic_selected_current_retained_not_trimmed")
    try:
        y = [log10_fraction(v) for v in current]
        if len(set(current)) != len(set(y)):
            return reject("log_precision_merges_distinct_currents")
        n = len(x)
        x_mean, y_mean = sum(x) / n, sum(y) / n
        dx, dy = [v - x_mean for v in x], [v - y_mean for v in y]
        sxx = sum(v * v for v in dx)
        if sxx == 0:
            return reject("rank_deficient_gate_voltage")
        slope = sum(a * b for a, b in zip(dx, dy)) / sxx
        intercept = y_mean - slope * x_mean
        predicted = [y_mean + slope * v for v in dx]
        residuals = [a - b for a, b in zip(y, predicted)]
        sse, syy = sum(v * v for v in residuals), sum(v * v for v in dy)
        fit = {"method": "unweighted_ordinary_least_squares_log10_current_on_vgs",
               "slope_decades_per_V": finite_float(slope),
               "zero_gate_intercept_log10_current_over_A": finite_float(intercept),
               "mean_gate_source_voltage_V": finite_float(x_mean),
               "mean_log10_current_over_A": finite_float(y_mean),
               "r_squared": finite_float(1 - sse / syy) if syy else None,
               "rmse_decades": root_fraction(sse / n),
               "max_abs_residual_decades": finite_float(max(abs(v) for v in residuals)),
               "selected_gate_voltage_range_V": [finite_float(min(x)), finite_float(max(x))],
               "selected_gate_voltage_span_V": finite_float(max(x) - min(x)),
               "selected_signed_current_range_A": [finite_float(min(p["id"] for p in selected)), finite_float(max(p["id"] for p in selected))],
               "observed_log10_current_span_decades": finite_float(max(y) - min(y))}
        reciprocal = ({"signed_V_per_decade": finite_float(1 / slope),
                       "signed_mV_per_decade": finite_float(1000 / slope),
                       "magnitude_mV_per_decade": finite_float(abs(1000 / slope)),
                       "interpretation": "reciprocal_descriptive_slope_not_certified_subthreshold_swing"}
                      if slope else None)
        fitted_by_row = {p["source_row"]: {"log10_declared_polarity_current_over_A": finite_float(actual),
                                         "fitted_log10_current_over_A": finite_float(fitted),
                                         "residual_decades": finite_float(residual)}
                         for p, actual, fitted, residual in zip(selected, y, predicted, residuals)}
    except (InputError, DecimalException, OverflowError, ValueError):
        return reject("derived_numeric_range_error")
    warnings.append("nonzero_residuals_may_include_log_rounding_noise_or_curvature" if sse else
                    "zero_residuals_do_not_validate_subthreshold_regime")
    if not syy:
        warnings.append("constant_log_current_r_squared_undefined")
    if not slope:
        warnings.append("zero_log_slope_reciprocal_undefined")
    if slope < 0:
        warnings.append("negative_log_slope_sign_preserved")
    if not min(x) <= 0 <= max(x):
        warnings.append("zero_gate_intercept_is_extrapolated_diagnostic_only")
    for raw in result["observations"]:
        if raw["source_row"] in fitted_by_row:
            raw.update(fitted_by_row[raw["source_row"]])
            raw["used_in_fit"] = True
    result.update({"status": "ok", "fit": fit, "reciprocal_slope": reciprocal})
    return result


def evaluate(path, voltage_unit, current_unit, window_min, window_max,
             current_polarity, assumptions_accepted=False):
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of log-transfer assumptions is required")
    if voltage_unit not in VOLTAGE_UNITS or current_unit not in CURRENT_UNITS:
        raise InputError("unsupported input unit")
    if current_polarity not in CURRENT_POLARITIES:
        raise InputError("current polarity must be explicitly positive or negative")
    lower = number(window_min, VOLTAGE_UNITS[voltage_unit])
    upper = number(window_max, VOLTAGE_UNITS[voltage_unit])
    if lower >= upper:
        raise InputError("user voltage-window minimum must be strictly less than maximum")
    groups, rows, digest = load_csv(path, voltage_unit, current_unit)
    results = [extract_group(key, points, lower, upper, current_polarity) for key, points in groups.items()]
    reasons = {}
    for item in results:
        if item["status"] == "rejected":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    rejected = sum(reasons.values())
    return {"schema_version": 1, "tool": "transfer-log-slope", "version": VERSION,
            "input_units": {"gate_drain_body_voltage": voltage_unit, "drain_current": current_unit, "temperature": "K"},
            "provenance": {"input_sha256": digest,
                           "source_row_convention": "CSV physical line at end of record, header included",
                           "group_columns": list(GROUP_COLUMNS), "references": SOURCES},
            "parameters": {"log_transfer_assumptions_declared": True,
                           "current_polarity": current_polarity, "current_normalization": "none",
                           "window_min_input": window_min.strip(), "window_max_input": window_max.strip(),
                           "window_min_V": finite_float(lower), "window_max_V": finite_float(upper),
                           "window_inclusive": True, "minimum_selected_samples": 3,
                           "window_search": "not_performed", "row_weighting": "equal",
                           "log_base": 10, "log_reference_current_A": 1,
                           "log_decimal_precision": LOG_PRECISION},
            "counts": {"input_rows": rows, "input_groups": len(groups),
                       "distinct_physical_device_ids": len({key[:3] for key in groups}),
                       "inside_window_rows": sum(r["selected_sample_count"] for r in results),
                       "outside_window_rows": sum(r["sample_count"] - r["selected_sample_count"] for r in results),
                       "fitted_rows": sum(r["selected_sample_count"] for r in results if r["status"] == "ok"),
                       "extracted_groups": len(results) - rejected, "rejected_groups": rejected,
                       "rejected_by_reason": reasons},
            "assumptions_and_limits": ASSUMPTIONS, "results": results}


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
    result = SafeParser(description="Fit signed log10 current versus VGS within an explicit user voltage window, separately per branch; not automatic subthreshold extraction.", allow_abbrev=False)
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS,
                        help="Shared unit for VGS, VDS, VBS and both window bounds")
    result.add_argument("--current-unit", required=True, choices=CURRENT_UNITS)
    result.add_argument("--window-min", required=True, help="Inclusive VGS lower bound in --voltage-unit")
    result.add_argument("--window-max", required=True, help="Inclusive VGS upper bound in --voltage-unit")
    result.add_argument("--current-polarity", required=True, choices=CURRENT_POLARITIES)
    result.add_argument("--accept-log-transfer-assumptions", required=True, action="store_true",
                        help="Declare source-referenced fixed-condition branches and independently chosen window, not a certified subthreshold regime")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create new owner-only JSON; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print raw IDs, numeric text, observations and fit diagnostics")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.voltage_unit, args.current_unit, args.window_min,
                          args.window_max, args.current_polarity, args.accept_log_transfer_assumptions)
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
