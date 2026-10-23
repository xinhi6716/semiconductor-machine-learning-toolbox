#!/usr/bin/env python3
"""Signed integral of a supplied voltage-closed I-V path; never energy or causal validation.

Explicit outgoing-edge branches, exact rational arithmetic and complete row audit.
Standard library only; no sorting, added closure, sign repair, fitting or network.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
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
GROUP_COLUMNS = ("lot", "wafer", "device", "condition", "cycle", "replicate")
COLUMNS = (*GROUP_COLUMNS, "sample_id", "branch_to_next", "voltage", "current")
VOLTAGE_UNITS = {"V": Fraction(1), "mV": Fraction(1, 1000),
                 "uV": Fraction(1, 10**6), "nV": Fraction(1, 10**9)}
CURRENT_UNITS = {"A": Fraction(1), "mA": Fraction(1, 1000),
                 "uA": Fraction(1, 10**6), "nA": Fraction(1, 10**9), "pA": Fraction(1, 10**12)}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_GROUPS = 1000
MAX_GROUP_ROWS = 1000
MAX_RATIONAL_BITS = 4096
MAX_EXACT_CHARACTERS = 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MIN_INPUT = Fraction(1, 10**150)
MAX_INPUT = Fraction(10**150)
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")
BASE_WARNINGS = [
    "supplied_order_not_verified_chronology",
    "conditions_are_declarations",
    "signed_cancellation_possible_self_intersection_not_tested",
    "not_energy_intrinsic_hysteresis_or_causal_validation",
    "feature_timing_unverified",
]
LIMITS = [
    "This is the signed trapezoidal integral of the supplied piecewise-straight I-V path, in A V, not electrical energy, physical power, unsigned geometric lobe area, intrinsic hysteresis or causal validation. No physical integration error bound is supplied.",
    "Voltage endpoints must match exactly; current endpoint equality is reported separately. A voltage-closed path with unequal currents is not an observed closed I-V polygon. No endpoint, edge, interpolation sample or closure is added.",
    "Every supplied observation is retained. branch_to_next owns the edge from its row to the next row in the same declared loop; the final field is empty. Shared branch endpoints are one observation, not independent measurements.",
    "Branch labels are declared, contiguous and nonreversing in voltage, with flats allowed. This conservative branch contract is not a mathematical requirement of the integral. No cycles, physical states or branches are inferred; no sorting, sign repair, smoothing, trimming or averaging is performed.",
    "A V has the dimension of W but this path descriptor is not electrical power. Terminal energy requires a justified time-domain integral of V(t) I(t) dt in J. Constant-voltage dwell edges contribute zero here but can consume energy.",
    "Signed lobe contributions may cancel. A zero result does not establish absent hysteresis, and a nonzero result does not establish a mechanism. Self-intersections and lobe areas are not calculated.",
    "Adding a constant current offset changes the total by offset times (last voltage minus first voltage); invariance applies only to exactly voltage-closed totals, not individual branches, branch-specific drift, or calibration errors.",
    "Exact decimal-to-rational SI arithmetic determines comparisons and sums. Exact decimal strings are authoritative; binary64 summaries can round or alias distinct values. Nonzero-to-zero or nonfinite summary conversion rejects the entire loop, never clips or fills it.",
    "Device, condition, cycle, replicate and supplied order are declarations, not verified chronology, fixed laboratory conditions, independent devices, or actual physical closure. Retain instrument records, protocol and adapter lineage separately; the hash covers only this input CSV.",
    "The feature requires the complete loop and every used annotation. Availability is not established before the final sample; time_verified is false. Future prediction requires separate evidence that the full feature existed by the prediction origin and the target occurs strictly later; labels are not timestamps and repeats are not independent devices.",
]


class InputError(ValueError):
    """Fatal diagnostic that never includes raw identifiers, values or paths."""


class NumericRangeError(ValueError):
    """A loop summary cannot be represented as a finite non-underflowed float."""


def bounded(value):
    if (abs(value.numerator).bit_length() > MAX_RATIONAL_BITS
            or value.denominator.bit_length() > MAX_RATIONAL_BITS):
        raise InputError("numeric resource limit prevents a complete exact audit")
    return value


def exact_decimal(value):
    """Canonical terminating-decimal string without Decimal-context arithmetic."""
    value = bounded(value)
    if not value:
        return "0"
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise InputError("exact audit requires terminating decimal arithmetic")
    places = max(twos, fives)
    coefficient = abs(value.numerator) * 2**(places - twos) * 5**(places - fives)
    exponent = -places
    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    digits = str(coefficient)
    exponent += len(digits) - 1
    result = ("-" if value < 0 else "") + digits[0]
    if len(digits) > 1:
        result += "." + digits[1:]
    result += f"e{exponent:+d}"
    if len(result) > MAX_EXACT_CHARACTERS:
        raise InputError("numeric resource limit prevents a complete exact audit")
    return result


def finite_float(value):
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise NumericRangeError from None
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise NumericRangeError
    return 0.0 if value == 0 else result


def identifier(value):
    if (not value or len(value) > 128 or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InputError("identifiers must be nonempty, unpadded and free of ASCII controls")
    return value


def number(text, factor):
    if len(text) > 64 or not NUMBER.fullmatch(text):
        raise InputError("numeric fields must be unpadded finite decimal numbers")
    value = Decimal(text)
    if len(value.as_tuple().digits) > 32 or abs(value.as_tuple().exponent) > 200:
        raise InputError("numeric field exceeds decimal precision or exponent limits")
    scaled = bounded(Fraction(value) * factor)
    if scaled and not MIN_INPUT <= abs(scaled) <= MAX_INPUT:
        raise InputError("nonzero SI inputs must have magnitude between 1e-150 and 1e150")
    return scaled


def load_csv(path, voltage_factor, current_factor):
    try:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except ValueError:
            # The callable API can receive an embedded-NUL path; argv cannot.
            raise InputError("unable to read a valid regular UTF-8 CSV input") from None
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InputError("input must be a regular CSV file")
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputError("CSV exceeds the input byte limit")
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, None)
        if header is None or len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise InputError("CSV header must contain exactly the documented I-V loop columns")
        groups = {}
        previous_key = None
        seen_samples = set()
        rows = 0
        while True:
            line_start = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if len(row) != len(header):
                raise InputError("CSV row width must match the header; blank records are not allowed")
            record = dict(zip(header, row))
            key = tuple(identifier(record[c]) for c in GROUP_COLUMNS)
            if key != previous_key:
                if key in groups:
                    raise InputError("a declared loop must occupy one contiguous block of records")
                groups[key] = []
                previous_key = key
                seen_samples = set()
            sample_id = identifier(record["sample_id"])
            if sample_id in seen_samples:
                raise InputError("sample IDs must be unique within each declared loop")
            seen_samples.add(sample_id)
            branch = record["branch_to_next"]
            if branch:
                identifier(branch)
            rows += 1
            groups[key].append({"sample_id": sample_id, "branch_to_next": branch,
                                "voltage_text": record["voltage"], "current_text": record["current"],
                                "voltage": number(record["voltage"], voltage_factor),
                                "current": number(record["current"], current_factor),
                                "source_record_index": rows, "source_line_start": line_start,
                                "source_line_end": reader.line_num})
            if rows > MAX_ROWS or len(groups) > MAX_GROUPS or len(groups[key]) > MAX_GROUP_ROWS:
                raise InputError("CSV exceeds row, loop or rows-per-loop limits")
        if not rows:
            raise InputError("CSV contains no observations")
        for points in groups.values():
            if points[-1]["branch_to_next"] or any(not p["branch_to_next"] for p in points[:-1]):
                raise InputError("every nonfinal row needs an outgoing branch; the final row must be empty")
    except (OSError, UnicodeError, csv.Error, TypeError):
        raise InputError("unable to read a valid regular UTF-8 CSV input") from None
    return groups, rows, len(raw), hashlib.sha256(raw).hexdigest()


def audit_group(key, points):
    observations = []
    for i, p in enumerate(points, 1):
        observations.append({k: v for k, v in p.items() if k not in ("voltage", "current")}
                            | {"sample_index": i, "voltage_V_exact": exact_decimal(p["voltage"]),
                               "current_A_exact": exact_decimal(p["current"]), "branch_block_indices": []})
    edges, branches, branch_sums, signs = [], [], [], []
    total = Fraction(0)
    for i, (left, right) in enumerate(zip(points, points[1:]), 1):
        delta = bounded(right["voltage"] - left["voltage"])
        mean = bounded(bounded(left["current"] + right["current"]) / 2)
        term = bounded(mean * delta)
        direction = "increasing" if delta > 0 else "decreasing" if delta < 0 else "flat"
        label = left["branch_to_next"]
        if not branches or branches[-1]["branch_id"] != label:
            branches.append({"branch_block_index": len(branches) + 1, "branch_id": label,
                             "edge_start": i, "edge_end": i, "flat_edge_count": 0})
            branch_sums.append(Fraction(0))
            signs.append(set())
        block = branches[-1]
        block["edge_end"] = i
        block["flat_edge_count"] += int(delta == 0)
        if delta:
            signs[-1].add(direction)
        branch_sums[-1] = bounded(branch_sums[-1] + term)
        total = bounded(total + term)
        edges.append({"edge_index": i, "left_sample_index": i, "right_sample_index": i + 1,
                      "branch_block_index": block["branch_block_index"], "branch_id": label,
                      "direction": direction, "delta_voltage_V_exact": exact_decimal(delta),
                      "trapezoid_A_V_exact": exact_decimal(term)})
    for branch, value, directions in zip(branches, branch_sums, signs):
        a, b = branch["edge_start"], branch["edge_end"]
        branch.update({"edge_count": b - a + 1, "sample_indices": list(range(a, b + 2)),
                       "direction": "mixed" if len(directions) == 2 else next(iter(directions)) if directions else "flat",
                       "signed_path_integral_A_V_exact": exact_decimal(value),
                       "signed_path_integral_A_V": None})
        for i in branch["sample_indices"]:
            observations[i - 1]["branch_block_indices"].append(branch["branch_block_index"])
    shared = [{"sample_index": p["sample_index"], "sample_id": p["sample_id"],
               "branch_block_indices": p["branch_block_indices"][:]} for p in observations
              if len(p["branch_block_indices"]) == 2]
    voltage_diff = bounded(points[-1]["voltage"] - points[0]["voltage"])
    current_diff = bounded(points[-1]["current"] - points[0]["current"])
    span = bounded(max(p["voltage"] for p in points) - min(p["voltage"] for p in points))
    reasons = []
    if len(points) < 3:
        reasons.append("insufficient_samples")
    if span == 0:
        reasons.append("zero_voltage_span")
    if voltage_diff != 0:
        reasons.append("voltage_endpoints_not_equal")
    if len({b["branch_id"] for b in branches}) != len(branches):
        reasons.append("noncontiguous_declared_branch")
    if any(len(s) == 2 for s in signs):
        reasons.append("reversing_declared_branch")
    metrics = None
    if not reasons:
        try:
            total_float = finite_float(total)
            branch_floats = [finite_float(value) for value in branch_sums]
        except NumericRangeError:
            reasons.append("derived_numeric_range_error")
        else:
            metrics = {"signed_path_integral_A_V": total_float,
                       "signed_path_integral_A_V_exact": exact_decimal(total)}
            for branch, value in zip(branches, branch_floats):
                branch["signed_path_integral_A_V"] = value
    warnings = BASE_WARNINGS[:]
    if any(e["direction"] == "flat" for e in edges):
        warnings.append("flat_edges_retained")
    if shared:
        warnings.append("branch_endpoints_shared_observations")
    if voltage_diff == 0 and current_diff != 0:
        warnings.append("voltage_closed_current_endpoints_differ")
    if metrics is not None and total == 0:
        warnings.append("zero_signed_integral_not_absence_of_hysteresis")
        if any(e["trapezoid_A_V_exact"] != "0" for e in edges):
            warnings.append("nonzero_edge_contributions_cancel_exactly")
    return {"key": dict(zip(GROUP_COLUMNS, key)), "status": "rejected" if reasons else "usable",
            "rejection_reasons": reasons, "primary_rejection_reason": reasons[0] if reasons else None,
            "sample_count": len(points), "edge_count": len(edges), "observations": observations,
            "edges": edges, "branch_audits": branches, "shared_endpoints": shared,
            "closure": {"voltage_endpoint_difference_V_exact": exact_decimal(voltage_diff),
                        "current_endpoint_difference_A_exact": exact_decimal(current_diff),
                        "voltage_closed_exact": voltage_diff == 0, "current_endpoints_equal_exact": current_diff == 0,
                        "iv_endpoint_closed_exact": voltage_diff == current_diff == 0, "added_closure_edge": False},
            "voltage_span_V_exact": exact_decimal(span), "metrics": metrics,
            "feature_availability": {"earliest_required_sample_index": len(points),
                                     "earliest_required_sample_id": points[-1]["sample_id"], "time_verified": False,
                                     "prediction_eligibility": "not_established",
                                     "required_boundary": "complete_feature_available_at_or_before_prediction_origin_and_target_strictly_after_origin"},
            "warnings": warnings}


def report_chunks(report):
    """Bound UTF-8 serialization before any output is opened or written."""
    encoder = json.JSONEncoder(indent=2, ensure_ascii=False, allow_nan=False)
    size = 0
    for text in encoder.iterencode(report):
        chunk = text.encode("utf-8")
        size += len(chunk)
        if size + 1 > MAX_REPORT_BYTES:  # Reserve the final newline.
            raise InputError("serialized report exceeds the output byte limit")
        yield chunk
    yield b"\n"


def evaluate(path, *, voltage_unit, current_unit, accept_supplied_path_assumptions=False):
    if (not isinstance(voltage_unit, str) or voltage_unit not in VOLTAGE_UNITS
            or not isinstance(current_unit, str) or current_unit not in CURRENT_UNITS):
        raise InputError("explicit supported voltage and current units are required")
    if accept_supplied_path_assumptions is not True:
        raise InputError("explicit acknowledgment of the supplied-path assumptions is required")
    groups, rows, byte_count, digest = load_csv(path, VOLTAGE_UNITS[voltage_unit], CURRENT_UNITS[current_unit])
    results = [audit_group(key, points) for key, points in groups.items()]
    rejected = sum(r["status"] == "rejected" for r in results)
    report = {"schema_version": 1, "tool": "iv-loop-integral", "version": VERSION,
              "parameters": {"voltage_unit": voltage_unit, "current_unit": current_unit,
                             "accept_supplied_path_assumptions": True,
                             "closure_rule": "exact_voltage_endpoints_no_added_edge",
                             "arithmetic": "exact_decimal_to_rational_then_binary64_summary",
                             "branch_rule": "explicit_outgoing_edge_labels_contiguous_nonreversing"},
              "provenance": {"input_sha256": digest, "input_byte_count": byte_count,
                             "group_columns": list(GROUP_COLUMNS),
                             "source_record_convention": "one-based data record index excluding header",
                             "source_line_convention": "one-based physical CSV lines including header",
                             "sample_index_convention": "one-based within declared loop",
                             "edge_index_convention": "edge i joins sample i to sample i+1; no added edge"},
              "counts": {"input_samples": rows, "input_loops": len(results),
                         "usable_loops": len(results) - rejected, "rejected_loops": rejected,
                         "input_edges": sum(r["edge_count"] for r in results),
                         "input_branch_blocks": sum(len(r["branch_audits"]) for r in results)},
              "assumptions_and_limits": LIMITS[:], "results": results}
    for _ in report_chunks(report):
        pass  # The callable API also enforces its serialized-report resource cap.
    return report


def write_report(report, output=None):
    payload = b"".join(report_chunks(report))
    if output is None:
        sys.stdout.write(payload.decode("utf-8"))
        return
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "error: invalid command-line arguments; use --help for the documented options\n")


def parser():
    result = SafeParser(description="Signed integral of an explicitly branched, exactly voltage-closed I-V path; not energy or intrinsic hysteresis.", allow_abbrev=False)
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--voltage-unit", required=True, choices=VOLTAGE_UNITS)
    result.add_argument("--current-unit", required=True, choices=CURRENT_UNITS)
    result.add_argument("--accept-supplied-path-assumptions", action="store_true",
                        help="Acknowledge supplied-order path and interpretation limits, not scientific validity")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create new owner-only JSON; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print raw IDs, values and complete audit")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, voltage_unit=args.voltage_unit, current_unit=args.current_unit,
                          accept_supplied_path_assumptions=args.accept_supplied_path_assumptions)
        write_report(report, args.output)
    except InputError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("error: unable to write a complete new JSON report; existing outputs are never overwritten", file=sys.stderr)
        return 2
    return 1 if report["counts"]["rejected_loops"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
