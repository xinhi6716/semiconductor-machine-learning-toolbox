#!/usr/bin/env python3
"""Offline BOSCH v1 identity inventory and exact channel-name audit.

Original standard-library software. No network, NetCDF library, scientific
numeric decoder, imputation, row exclusion, feature extraction or model fitting.
"""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata

HEADER = ("experiment_key", "lot_number", "wafer_number", "loc_id", "X", "Y",
          "preox_thickness", "postox_thickness", "stepheight", "oxide_etch", "si_etch")
IDENTITY_FIELDS = HEADER[:4]
MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
MAX_RECORD_BYTES = 65536
MAX_LABEL_BYTES = 256
MAX_GROUPS = 128
MAX_REPORT_BYTES = 16 * 1024 * 1024
EXPERIMENT = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})_([0-9]{2})\Z", re.ASCII)
GROUP = re.compile(r"/Day_([0-9]{4})_([0-9]{2})_([0-9]{2})_Wafer_([0-9]{2})\Z", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class InputError(ValueError):
    """Categorical diagnostic that must not contain source values or paths."""


def identity_text(raw):
    if len(raw) > MAX_LABEL_BYTES:
        raise InputError("Identity byte limit exceeded")
    try:
        value = raw.decode("utf-8", "strict")
    except UnicodeError as error:
        raise InputError("Invalid identity UTF-8") from error
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise InputError("Control character in identity")
    return None if value == "" else value


def identity_records(blob):
    """Decode only the header and columns 0..3. Scan other bytes for CSV framing.

    Logical row ordinals are one-based excluding the header. Exact empty fields
    become None. No strip, case folding, integer conversion, filling or dropping.
    """
    if not isinstance(blob, bytes) or len(blob) > MAX_CSV_BYTES:
        raise InputError("CSV byte/type limit exceeded")
    position = 3 if blob.startswith(b"\xef\xbb\xbf") else 0
    ordinal = 0
    rows = []
    while position < len(blob):
        start = position
        field = 0
        kept = {}
        while True:
            keep = ordinal == 0 or field < 4
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
                    if position - start > MAX_RECORD_BYTES or (keep and len(cell) > MAX_LABEL_BYTES):
                        raise InputError("CSV record/selected-field limit exceeded")
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
                    if position - start > MAX_RECORD_BYTES or (keep and len(cell) > MAX_LABEL_BYTES):
                        raise InputError("CSV record/selected-field limit exceeded")
            if keep:
                kept[field] = bytes(cell)
            field += 1
            if field > len(HEADER) or position - start > MAX_RECORD_BYTES:
                raise InputError("CSV width/record limit exceeded")
            if position < len(blob) and blob[position] == 44:
                position += 1
                continue
            if field != len(HEADER):
                raise InputError("CSV width mismatch")
            if position < len(blob) and blob[position] == 13:
                if position + 1 >= len(blob) or blob[position + 1] != 10:
                    raise InputError("Bare CSV carriage return")
                position += 2
            elif position < len(blob) and blob[position] == 10:
                position += 1
            break
        if ordinal == 0:
            if tuple(kept[index] for index in range(len(HEADER))) != tuple(name.encode("ascii") for name in HEADER):
                raise InputError("Exact BOSCH v1 header mismatch")
        else:
            if ordinal > MAX_ROWS:
                raise InputError("CSV row limit exceeded")
            record = {"logical_data_row_ordinal": ordinal}
            record.update({name: identity_text(kept[index]) for index, name in enumerate(IDENTITY_FIELDS)})
            rows.append(record)
        ordinal += 1
    if ordinal < 2:
        raise InputError("CSV needs a header and at least one data row")
    return rows


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_):
    raise InputError("Nonstandard JSON constant")


def _named_date(match):
    try:
        date(*(int(match.group(i)) for i in (1, 2, 3)))
    except ValueError:
        return False
    return match.group(4) != "00"


def channel_metadata(blob):
    """Validate a user-supplied label-only sidecar, never open its claimed source."""
    if not isinstance(blob, bytes) or len(blob) > MAX_METADATA_BYTES:
        raise InputError("Metadata byte/type limit exceeded")
    try:
        metadata = json.loads(blob.decode("utf-8", "strict"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InputError("Invalid metadata JSON") from error
    if not isinstance(metadata, dict) or set(metadata) != {"schema_version", "process_file_sha256", "canonical_labels", "groups"}:
        raise InputError("Metadata top-level schema mismatch")
    if metadata["schema_version"] != "bosch-channel-metadata-v1":
        raise InputError("Metadata schema version mismatch")
    digest = metadata["process_file_sha256"]
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise InputError("Declared process-file digest syntax mismatch")
    canonical = metadata["canonical_labels"]
    if not isinstance(canonical, list) or len(canonical) != 31:
        raise InputError("Exactly 31 ordered canonical labels required")
    for label in canonical:
        if not isinstance(label, str):
            raise InputError("Canonical label must be text")
        try:
            if identity_text(label.encode("utf-8", "strict")) is None:
                raise InputError("Empty canonical label")
        except UnicodeError as error:
            raise InputError("Invalid canonical label encoding") from error
    if len(set(canonical)) != len(canonical):
        raise InputError("Duplicate canonical label")
    groups = metadata["groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
        raise InputError("Metadata group-count limit")
    seen = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"group_path", "feature_labels"}:
            raise InputError("Metadata group schema mismatch")
        path = group["group_path"]
        match = GROUP.fullmatch(path) if isinstance(path, str) else None
        if not match or not _named_date(match) or path in seen:
            raise InputError("Invalid or duplicate BOSCH group name")
        seen.add(path)
        labels = group["feature_labels"]
        if not isinstance(labels, list) or len(labels) not in (31, 44):
            raise InputError("BOSCH feature width must be 31 or 44")
        for label in labels:
            if not isinstance(label, str):
                raise InputError("Feature label must be text")
            try:
                decoded = identity_text(label.encode("utf-8", "strict"))
            except UnicodeError as error:
                raise InputError("Invalid feature label encoding") from error
            if decoded is None:
                raise InputError("Empty feature label")
        if len(set(labels)) != len(labels):
            raise InputError("Duplicate feature label")
    return metadata


def channel_alignment(metadata):
    groups = metadata["groups"]
    name_sets = [set(group["feature_labels"]) for group in groups]
    common = sorted(set.intersection(*name_sets))
    union = sorted(set.union(*name_sets))
    aligned = []
    for group in groups:
        labels = group["feature_labels"]
        positions = {label: index for index, label in enumerate(labels)}
        aligned.append({"group_path": group["group_path"], "feature_count": len(labels),
                        "feature_labels_in_source_order": labels,
                        "canonical_name_column_map": [{"label": label, "zero_based_column_index": positions.get(label)}
                                                      for label in metadata["canonical_labels"]],
                        "missing_canonical_labels": [label for label in metadata["canonical_labels"] if label not in positions],
                        "noncanonical_labels_in_source_order": [label for label in labels if label not in metadata["canonical_labels"]],
                        "common_name_column_map": [{"label": label, "zero_based_column_index": positions[label]}
                                                   for label in common],
                        "noncommon_labels_in_source_order": [label for label in labels if label not in common]})
    return {"ordering": "Unicode codepoint lexical order; exact names, no normalization",
            "canonical_labels_in_declared_order": metadata["canonical_labels"],
            "common_labels": common, "union_labels": union,
            "common_label_count": len(common), "union_label_count": len(union),
            "groups": aligned, "sensor_values_read": False,
            "physical_units_or_channel_roles_verified": False,
            "automatic_feature_selection": False}


def inventory(rows, metadata):
    duplicates = {}
    wafer_keys = {}
    unresolved = []
    for row in rows:
        ordinal = row["logical_data_row_ordinal"]
        key = tuple(row[name] for name in IDENTITY_FIELDS)
        duplicates.setdefault(key, []).append(ordinal)
        wafer = key[:3]
        if any(value is None for value in wafer):
            unresolved.append(ordinal)
        else:
            wafer_keys.setdefault(wafer, []).append(row)
    process_paths = {group["group_path"] for group in metadata["groups"]}
    results = []
    proposed = {}
    for (experiment, lot, wafer), records in wafer_keys.items():
        match = EXPERIMENT.fullmatch(experiment)
        path = None
        if not match or not _named_date(match):
            status = "UNSUPPORTED_EXPERIMENT_KEY"
        elif wafer != str(int(match.group(4))):
            status = "WAFER_NUMBER_TEXT_MISMATCH"
        else:
            year, month, day, number = match.groups()
            path = f"/Day_{year}_{month}_{day}_Wafer_{number}"
            status = "EXACT_NAME_CANDIDATE" if path in process_paths else "NO_EXACT_PROCESS_GROUP"
        locations = [row["loc_id"] for row in records]
        counts = Counter(locations)
        result = {"experiment_key": experiment, "lot_number": lot, "wafer_number": wafer,
                  "source_row_ordinals": [row["logical_data_row_ordinal"] for row in records],
                  "location_labels_in_row_order": locations,
                  "distinct_nonnull_location_count": len(set(locations) - {None}),
                  "null_location_count": counts.get(None, 0),
                  "duplicate_nonnull_location_labels": [name for name, count in counts.items() if name is not None and count > 1],
                  "proposed_process_group": path, "name_join_status": status,
                  "finite_target_eligibility_verified": False}
        results.append(result)
        if status == "EXACT_NAME_CANDIDATE":
            proposed.setdefault(path, []).append(result)
    for candidates in proposed.values():
        if len(candidates) > 1:
            for candidate in candidates:
                candidate["name_join_status"] = "AMBIGUOUS_MULTIPLE_IDENTITY_KEYS"
    named = set(proposed)
    return {"rows": rows, "row_count": len(rows),
            "null_counts": {name: sum(row[name] is None for row in rows) for name in IDENTITY_FIELDS},
            "exact_duplicate_four_field_tuples": [{"identity": dict(zip(IDENTITY_FIELDS, key)), "source_row_ordinals": ordinals}
                                                  for key, ordinals in duplicates.items() if len(ordinals) > 1],
            "incomplete_wafer_key_row_ordinals": unresolved,
            "complete_identity_keys": results,
            "complete_identity_key_count": len(results),
            "process_groups_without_complete_identity_name_candidate": [group["group_path"] for group in metadata["groups"]
                                                                         if group["group_path"] not in named],
            "rows_dropped_or_filled": 0, "physical_wafer_identity_or_independence_verified": False}


def audit(csv_bytes, metadata_bytes):
    rows = identity_records(csv_bytes)
    metadata = channel_metadata(metadata_bytes)
    report = {"schema_version": "bosch-identity-audit-v1",
              "status": "COMPLETE_METADATA_AUDIT_NOT_SCIENTIFIC_VALIDATION",
              "dataset_reference": "https://doi.org/10.5281/zenodo.17122442",
              "provenance": {"csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
                             "csv_bytes": len(csv_bytes),
                             "channel_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                             "channel_metadata_bytes": len(metadata_bytes),
                             "declared_process_file_sha256": metadata["process_file_sha256"],
                             "declared_process_hash_verified_against_file": False},
              "identity_inventory": inventory(rows, metadata),
              "channel_alignment": channel_alignment(metadata),
              "limitations": [
                  "CSV bytes are read and hashed; only header and four identity columns are decoded.",
                  "Other CSV fields are traversed for quoting/delimiters, not decoded, validated or emitted.",
                  "Channel sidecar is a user declaration; native Process_data.nc is not opened.",
                  "Name candidates are not verified physical joins, reset episodes or independent samples.",
                  "Location membership does not establish finite targets, metrology quality or eligibility.",
                  "No rows, duplicate identities, locations or channels are automatically excluded.",
                  "No target/sensor values, feature summaries, fitted models or causal estimates are produced."]}
    report_bytes(report)  # Enforce complete-report capacity before returning.
    return report


def read_regular(path, maximum):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise InputError("Input must be a bounded regular file")
            blob = stream.read(maximum + 1)
    except (OSError, ValueError) as error:
        if isinstance(error, InputError):
            raise
        raise InputError("Cannot read requested regular input") from error
    if len(blob) > maximum:
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
    """Finish buffered output before reporting success, even for a closed pipe."""
    try:
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
    except (OSError, ValueError):
        # A failed flush can retain bytes. Close the unusable stream so Python
        # does not retry that flush at shutdown and replace exit status 2 by 120.
        try:
            sys.stdout.close()
        except (OSError, ValueError):
            pass
        raise


def emit_failure(message):
    """Best-effort stderr only; a broken diagnostic sink must not mask exit 2."""
    stream = sys.stderr
    if stream is None:
        return  # print(file=None) would incorrectly fall back to stdout.
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        # Do not retry retained diagnostic bytes during interpreter shutdown.
        try:
            stream.close()
        except (OSError, ValueError):
            pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("Invalid command-line options; use --help")


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("csv", help="Local BOSCH v1 11-column CSV; only four identity columns are decoded")
    parser.add_argument("--channel-metadata", required=True, help="Local label-only JSON sidecar, not a NetCDF file")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--stdout", action="store_true", help="Explicitly print identity and channel metadata")
    destination.add_argument("--output", help="New report path; never overwrite")
    try:
        args = parser.parse_args(argv)
        report = audit(read_regular(args.csv, MAX_CSV_BYTES), read_regular(args.channel_metadata, MAX_METADATA_BYTES))
        payload = report_bytes(report)
        if args.stdout:
            write_stdout(payload)
        else:
            write_report(payload, args.output)
        return 0
    except InputError as error:
        emit_failure("Audit failed: " + str(error) + ". No complete report claimed.")
    except (OSError, ValueError, UnicodeError, RecursionError):
        emit_failure("Audit failed: input/output/serialization error. No complete report claimed.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
