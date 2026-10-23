#!/usr/bin/env python3
"""Describe signed transverse voltage versus magnetic field within Hall branches.

Standard library only; no instrument/network access, field-pair cancellation,
carrier-property inference, branch merging or physical-model validation.
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
COLUMNS = (*GROUP_COLUMNS, "temperature_K", "bias_current", "magnetic_field", "transverse_voltage")
FIELD_UNITS = {"T": "1", "mT": "0.001", "uT": "0.000001", "G": "0.0001", "kG": "0.1"}
VOLTAGE_UNITS = {"V": "1", "mV": "0.001", "uV": "0.000001", "nV": "0.000000001"}
CURRENT_UNITS = {"A": "1", "mA": "0.001", "uA": "0.000001", "nA": "0.000000001", "pA": "0.000000000001"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_GROUPS = 1000
MAX_GROUP_ROWS = 1000
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")
MIN_NUMBER = Fraction(1, 10 ** 150)
MAX_NUMBER = Fraction(10 ** 150)
SOURCES = [
    "https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/hall-effect",
    "https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/resistivity-and-hall",
    "https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/resistivity-and-hall/hall",
    "https://www.lakeshore.com/docs/default-source/product-downloads/manuals/hall_sensor-_manual.pdf",
]
ASSUMPTIONS = [
    "Each condition ID identifies a documented fixed measurement protocol, including current terminals and positive direction, ordered transverse voltage terminals (V_plus - V_minus), signed perpendicular field axis, device state, timing and calibration. The software cannot verify the protocol or geometry.",
    "Each lot/wafer/device/condition/replicate/branch is one acquisition-ordered strictly monotonic magnetic-flux-density sweep at fixed declared signed current and positive temperature in kelvin. Fields are B in tesla, not H in A/m. Branches and replicates are never merged or assumed to be independent devices.",
    "The descriptive fit is transverse_voltage = slope * magnetic_field + intercept, with equal row weights and field values treated as exact. It is not a causal fit or proof of a Hall mechanism, linearity, calibration, Ohmic contacts, thermal stability or measurement accuracy.",
    "The signed slope divided by the signed nonzero declared bias current is a current-normalized apparent Hall response in V/(A T). Zero-current groups retain a voltage-response diagnostic only. No absolute-value conversion or carrier-sign assignment is made.",
    "The fitted zero-field intercept is a diagnostic, not an isolated instrumental offset or a validated zero-field measurement. Contact misalignment, longitudinal pickup, thermoelectric contributions, drift, nonlinearities and other effects can remain in both slope and intercept. No offset subtraction or opposite-field/current pairing is performed.",
    "Nonzero residuals flag possible nonlinearity, noise, rounding or drift without distinguishing their cause or statistical significance. Zero residuals or high R-squared do not establish a valid physical model. RMSE uses N, not N-2, and is a descriptive residual scale, not uncertainty.",
    "No carrier density, carrier type, Hall coefficient, mobility, thickness, resistivity, transfer function outside the measured field range, confidence interval or population statistic is inferred.",
    "Exact decimal/rational arithmetic is used until JSON binary64 conversion. Keep the original input: JSON rounds values, and nonzero derived values outside finite nonzero binary64 range reject the group rather than being clipped.",
]


class InputError(ValueError):
    """Safe diagnostic with no raw identifiers, values or paths."""


def finite_float(value):
    try:
        result = float(value)
    except (ValueError, OverflowError, DecimalException):
        raise InputError("derived numeric value is outside the supported finite range") from None
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise InputError("derived numeric value is outside the supported finite range")
    return result


def number(text, factor):
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


def load_csv(path, field_unit, voltage_unit, current_unit):
    if field_unit not in FIELD_UNITS or voltage_unit not in VOLTAGE_UNITS or current_unit not in CURRENT_UNITS:
        raise InputError("unsupported input unit")
    try:
        # Nonblocking open rejects FIFOs without waiting for a writer.
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
            raise InputError("CSV header must contain exactly the documented Hall-response columns")
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
                     "current": number(record["bias_current"], CURRENT_UNITS[current_unit]),
                     "field": number(record["magnetic_field"], FIELD_UNITS[field_unit]),
                     "voltage": number(record["transverse_voltage"], VOLTAGE_UNITS[voltage_unit])}
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
    with localcontext() as context:
        context.prec = 50
        result = (Decimal(value.numerator) / Decimal(value.denominator)).sqrt()
    return finite_float(result)


def observation(point):
    return {"source_row": point["source_row"], "temperature_K": finite_float(point["temperature_K"]),
            "bias_current_A": finite_float(point["current"]),
            "magnetic_field_T": finite_float(point["field"]),
            "transverse_voltage_V": finite_float(point["voltage"])}


def extract_group(key, points):
    result = dict(zip(GROUP_COLUMNS, key))
    result.update({"status": "rejected", "sample_count": len(points),
                   "distinct_field_count": len({p["field"] for p in points}),
                   "source_rows": [p["source_row"] for p in points],
                   "observations": [observation(p) for p in points],
                   "fit": None, "current_normalized_sensitivity_V_per_A_T": None,
                   "warnings": ["descriptive_fit_not_physical_model_validation",
                                "branches_and_replicates_not_independent_devices",
                                "offset_and_non_hall_contributions_not_removed"]})

    def reject(reason):
        result["reason"] = reason
        return result

    if any(p["temperature_K"] <= 0 for p in points):
        return reject("nonpositive_temperature")
    if any(len({p[field] for p in points}) != 1 for field in ("temperature_K", "current")):
        return reject("inconsistent_temperature_or_bias_current")
    result["conditions"] = {"temperature_K": finite_float(points[0]["temperature_K"]),
                            "bias_current_A": finite_float(points[0]["current"])}
    if result["distinct_field_count"] != len(points):
        return reject("duplicate_magnetic_field")
    if len(points) < 3:
        return reject("fewer_than_three_distinct_fields")
    x = [p["field"] for p in points]
    y = [p["voltage"] for p in points]
    ascending = all(b > a for a, b in zip(x, x[1:]))
    descending = all(b < a for a, b in zip(x, x[1:]))
    if not (ascending or descending):
        return reject("nonmonotonic_magnetic_field")
    result["acquisition_direction"] = "increasing_field" if ascending else "decreasing_field"
    warnings = result["warnings"]
    for values, name in ((x, "field"), (y, "voltage")):
        if len(set(values)) != len({finite_float(v) for v in values}):
            warnings.append("binary64_" + name + "_rounding_merges_distinct_inputs")
    # Exact rational normal equations avoid subtractive binary64 cancellation.
    n = len(points)
    sx, sy = sum(x), sum(y)
    sxx = sum(v * v for v in x)
    sxy = sum(a * b for a, b in zip(x, y))
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n
    predicted = [slope * value + intercept for value in x]
    residuals = [actual - fitted for actual, fitted in zip(y, predicted)]
    sse = sum(value * value for value in residuals)
    syy = sum((value - sy / n) ** 2 for value in y)
    voltage_span = max(y) - min(y)
    extrapolation = (min(abs(v) for v in x) / (max(x) - min(x))
                     if min(x) > 0 or max(x) < 0 else Fraction(0))
    if extrapolation:
        warnings.append("zero_field_intercept_is_extrapolated")
    elif 0 not in x:
        warnings.append("zero_field_intercept_not_directly_sampled")
    if extrapolation >= 10:
        warnings.append("large_zero_field_extrapolation")
    if not (min(x) < 0 < max(x)):
        warnings.append("both_field_polarities_not_sampled")
    if sse:
        warnings.append("nonzero_residuals_possible_nonlinearity_noise_or_drift")
    else:
        warnings.append("zero_residuals_do_not_validate_linearity")
    if not syy:
        warnings.append("constant_voltage_r_squared_undefined")
    if not slope:
        warnings.append("zero_slope_does_not_establish_absence_of_hall_effect")
    current = points[0]["current"]
    if not current:
        warnings.append("zero_bias_current_normalization_unavailable")
    try:
        fit = {"method": "unweighted_ordinary_least_squares",
               "slope_V_per_T": finite_float(slope),
               "zero_field_intercept_V": finite_float(intercept),
               "r_squared": finite_float(1 - sse / syy) if syy else None,
               "rmse_V": root_fraction(sse / n),
               "max_abs_residual_V": finite_float(max(abs(v) for v in residuals)),
               "max_abs_residual_over_voltage_span": finite_float(max(abs(v) for v in residuals) / voltage_span) if voltage_span else None,
               "magnetic_field_range_T": [finite_float(min(x)), finite_float(max(x))],
               "zero_field_extrapolation_ratio": finite_float(extrapolation)}
        normalized = finite_float(slope / current) if current else None
        fitted_observations = []
        for raw, fitted, residual in zip(result["observations"], predicted, residuals):
            fitted_observations.append({**raw, "fitted_transverse_voltage_V": finite_float(fitted),
                                        "residual_V": finite_float(residual)})
    except InputError:
        return reject("derived_numeric_range_error")
    result.update({"status": "ok", "fit": fit, "observations": fitted_observations,
                   "current_normalized_sensitivity_V_per_A_T": normalized})
    return result


def evaluate(path, field_unit, voltage_unit, current_unit, assumptions_accepted=False):
    if assumptions_accepted is not True:
        raise InputError("explicit acceptance of Hall-response assumptions is required")
    groups, rows, digest = load_csv(path, field_unit, voltage_unit, current_unit)
    results = [extract_group(key, points) for key, points in groups.items()]
    reasons = {}
    for item in results:
        if item["status"] == "rejected":
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    rejected = sum(reasons.values())
    return {"schema_version": 1, "tool": "hall-response", "version": VERSION,
            "input_units": {"magnetic_field": field_unit, "transverse_voltage": voltage_unit,
                            "bias_current": current_unit, "temperature": "K"},
            "provenance": {"input_sha256": digest,
                           "source_row_convention": "CSV physical line at end of record, header included",
                           "group_columns": list(GROUP_COLUMNS), "references": SOURCES},
            "parameters": {"hall_response_assumptions_declared": True,
                           "minimum_distinct_fields": 3, "row_weighting": "equal",
                           "opposite_field_pairing": "not_performed"},
            "counts": {"input_rows": rows, "input_groups": len(groups),
                       "distinct_physical_device_ids": len({key[:3] for key in groups}),
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
    result = SafeParser(description="Describe signed transverse voltage versus B within separate fixed-current Hall branches; no carrier-property inference.", allow_abbrev=False)
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--field-unit", required=True, choices=FIELD_UNITS, help="Magnetic flux density B, not magnetic field strength H")
    result.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS)
    result.add_argument("--current-unit", required=True, choices=CURRENT_UNITS)
    result.add_argument("--accept-hall-assumptions", required=True, action="store_true",
                        help="Declare documented signed terminals/field axis, fixed current/temperature/protocol and separate monotonic branches; see README")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new owner-only JSON report; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print identifiers, observations and fit diagnostics")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.field_unit, args.voltage_unit, args.current_unit, args.accept_hall_assumptions)
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
