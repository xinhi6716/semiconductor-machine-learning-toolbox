#!/usr/bin/env python3
"""Audit ordered semiconductor sweep coordinates without inferring acquisition history.

Exact decimal comparisons partition adjacent edges, not independent observations.
No sorting, denoising, exclusions, fitting, branch export or network access.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

VERSION = "0.1.0"
GROUP_COLUMNS = ("lot", "wafer", "device", "condition", "sequence")
COLUMNS = (*GROUP_COLUMNS, "sample_id", "coordinate")
AXIS_UNITS = {
    "voltage": ("V", "mV", "uV", "nV"),
    "magnetic-flux-density": ("T", "mT", "uT", "G", "kG"),
}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_GROUPS = 1000
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z")
LIMITS = [
    "Order means supplied CSV row order, not verified acquisition chronology. A sequence is a user-declared contiguous record, not an inferred cycle or independent replicate.",
    "Segments partition adjacent coordinate edges into maximal increasing, decreasing or flat runs. Every edge belongs to exactly one segment. Adjacent segments reference the same boundary observation; repeated references are not new measurements.",
    "Comparisons use exact decimal values in the declared unit, without a tolerance, binary64 conversion, sorting, averaging, smoothing, deletion or interpolation. Equal-coordinate samples with distinct identities remain distinct observations.",
    "A flat run is separate; it is never assigned to an increasing or decreasing run. A singleton has one retained observation and zero edges or segments.",
    "Coordinate reversals, plateaus and segment counts do not establish physical hysteresis, switching states, acquisition cycles, stable conditions, or independent devices or observations.",
    "The audit is not an automatically valid branch input for I-V, constant-current Vth or Hall-response extraction. Verify chronology, fixed conditions and protocol separately, and preserve shared-endpoint lineage in any later adapter.",
]


class InputError(ValueError):
    """A diagnostic that never echoes raw identifiers, values or paths."""


def identifier(value):
    if (not value or value != value.strip() or len(value) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise InputError("identifiers must be nonempty, unpadded and free of ASCII controls")
    return value


def number(text):
    # Decimal construction and comparison do not round to the active context.
    # Do not subtract: only ordering is needed, even at extreme exponents.
    if len(text) > 64 or not NUMBER.fullmatch(text):
        raise InputError("coordinate must be an unpadded finite decimal number")
    value = Decimal(text)
    if len(value.as_tuple().digits) > 32 or abs(value.as_tuple().exponent) > 200:
        raise InputError("coordinate exceeds decimal precision or exponent limits")
    return value


def load_csv(path):
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
            raise InputError("CSV header must contain exactly the documented sweep-coordinate columns")
        groups = {}
        previous_key = None
        rows = 0
        sample_ids = set()
        while True:
            line_start = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if len(row) != len(header):
                raise InputError("CSV row width does not match the header; blank records are not allowed")
            record = dict(zip(header, row))
            key = tuple(identifier(record[name]) for name in GROUP_COLUMNS)
            if key != previous_key:
                if key in groups:
                    raise InputError("a declared sequence must occupy one contiguous block of CSV records")
                groups[key] = []
                previous_key = key
                sample_ids = set()
            sample_id = identifier(record["sample_id"])
            if sample_id in sample_ids:
                raise InputError("sample IDs must be unique within each declared sequence")
            sample_ids.add(sample_id)
            rows += 1
            groups[key].append({"sample_id": sample_id,
                                "source_record_index": rows,
                                "source_line_start": line_start,
                                "source_line_end": reader.line_num,
                                "coordinate_text": record["coordinate"],
                                "value": number(record["coordinate"])})
            if rows > MAX_ROWS or len(groups) > MAX_GROUPS:
                raise InputError("CSV exceeds row or sequence limits")
        if not rows:
            raise InputError("CSV contains no coordinate samples")
    except (OSError, UnicodeError, csv.Error):
        raise InputError("unable to read a valid UTF-8 CSV input") from None
    return groups, rows, hashlib.sha256(raw).hexdigest()


def direction(left, right):
    return "increasing" if right > left else "decreasing" if right < left else "flat"


def segment_sequence(key, points):
    """Partition n-1 edges; indices are one-based and local to this sequence."""
    samples = [{name: value for name, value in point.items() if name != "value"}
               | {"sample_index": index, "segment_ids": []}
               for index, point in enumerate(points, 1)]
    segments = []
    for edge_index, (left, right) in enumerate(zip(points, points[1:]), 1):
        sign = direction(left["value"], right["value"])
        if not segments or segments[-1]["direction"] != sign:
            segments.append({"segment_id": len(segments) + 1, "direction": sign,
                             "edge_start": edge_index, "edge_end": edge_index})
        else:
            segments[-1]["edge_end"] = edge_index
    for segment in segments:
        segment["edge_count"] = segment["edge_end"] - segment["edge_start"] + 1
        segment["sample_indices"] = list(range(segment["edge_start"], segment["edge_end"] + 2))
        for index in segment["sample_indices"]:
            samples[index - 1]["segment_ids"].append(segment["segment_id"])
    shared = [{"sample_index": p["sample_index"], "sample_id": p["sample_id"],
               "segment_ids": p["segment_ids"][:]} for p in samples if len(p["segment_ids"]) > 1]

    # Nonflat runs are either adjacent or separated by exactly one maximal flat
    # run. Report both endpoints of that flat interval, never one invented peak.
    reversals = []
    previous_nonflat = None
    for segment in segments:
        if segment["direction"] == "flat":
            continue
        if previous_nonflat and previous_nonflat["direction"] != segment["direction"]:
            start = previous_nonflat["edge_end"] + 1
            stop = segment["edge_start"]
            indices = list(range(start, stop + 1))
            reversals.append({"kind": "direct" if start == stop else "across_plateau",
                              "from_segment_id": previous_nonflat["segment_id"],
                              "to_segment_id": segment["segment_id"],
                              "turnaround_sample_indices": indices,
                              "turnaround_sample_ids": [samples[i - 1]["sample_id"] for i in indices]})
        previous_nonflat = segment
    warnings = ["supplied_order_is_not_verified_chronology",
                "segments_are_not_independent_observations_or_cycles"]
    if shared:
        warnings.append("adjacent_segments_share_endpoint_observations")
    if any(s["direction"] == "flat" for s in segments):
        warnings.append("equal_coordinate_observations_retained_as_flat_edges")
    if len(points) == 1:
        warnings.append("singleton_has_no_edges_or_segments")
    return {**dict(zip(GROUP_COLUMNS, key)), "samples": samples, "segments": segments,
            "shared_endpoints": shared, "coordinate_reversals": reversals,
            "unsegmented_sample_indices": [1] if len(points) == 1 else [],
            "warnings": warnings}


def evaluate(path, axis, coordinate_unit):
    if axis not in AXIS_UNITS or coordinate_unit not in AXIS_UNITS[axis]:
        raise InputError("coordinate unit must match a supported semiconductor sweep axis")
    groups, rows, digest = load_csv(path)
    results = [segment_sequence(key, points) for key, points in groups.items()]
    segments = [segment for item in results for segment in item["segments"]]
    return {"schema_version": 1, "tool": "sweep-segments", "version": VERSION,
            "axis": axis, "coordinate_unit": coordinate_unit,
            "provenance": {"input_sha256": digest, "group_columns": list(GROUP_COLUMNS),
                           "source_record_convention": "one-based data record index, excluding header",
                           "source_line_convention": "one-based physical CSV lines, including header",
                           "sample_index_convention": "one-based index within a declared sequence",
                           "edge_index_convention": "edge i connects sample i to sample i+1 within a declared sequence"},
            "parameters": {"comparison": "exact_decimal", "plateau_rule": "separate_flat_edge_runs",
                           "unit_conversion": "none; coordinate_text retains original declared-unit token",
                           "order": "supplied_contiguous_records", "noise_tolerance": "none"},
            "counts": {"input_samples": rows, "input_sequences": len(groups),
                       "edges": sum(len(points) - 1 for points in groups.values()),
                       "segments": len(segments),
                       "flat_edges": sum(s["edge_count"] for s in segments if s["direction"] == "flat"),
                       "shared_endpoint_samples": sum(len(r["shared_endpoints"]) for r in results),
                       "coordinate_reversals": sum(len(r["coordinate_reversals"]) for r in results),
                       "singleton_sequences": sum(len(points) == 1 for points in groups.values())},
            "assumptions_and_limits": LIMITS, "results": results}


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
    result = SafeParser(description="Audit exact ordered voltage or magnetic-flux-density edges; no acquisition chronology, independent cycles or physical hysteresis inferred.", allow_abbrev=False)
    result.add_argument("input", type=Path, help="UTF-8 CSV with exactly " + ",".join(COLUMNS))
    result.add_argument("--axis", required=True, choices=AXIS_UNITS,
                        help="Voltage includes declared terminal voltage or VGS; flux density is B, not H")
    result.add_argument("--coordinate-unit", required=True,
                        help="Voltage: V,mV,uV,nV; magnetic-flux-density: T,mT,uT,G,kG")
    destination = result.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new owner-only JSON audit; never overwrite")
    destination.add_argument("--stdout", action="store_true", help="Explicitly print identifiers, coordinates and mapping")
    result.add_argument("--version", action="version", version=VERSION)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = evaluate(args.input, args.axis, args.coordinate_unit)
        write_report(report, args.output)
    except InputError as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("error: unable to write a new JSON audit; existing outputs are never overwritten", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
