#!/usr/bin/env python3
"""Create invented BOSCH-shaped identity fixtures, never measurement data."""
import argparse
import csv
import io
import json
import os
from pathlib import Path


def fixture_bytes():
    fields = ["experiment_key", "lot_number", "wafer_number", "loc_id", "X", "Y",
              "preox_thickness", "postox_thickness", "stepheight", "oxide_etch", "si_etch"]
    locations = ["F10", "D8", "H8", "B6", "F6", "J6", "D4", "H4", "F2"]
    rows = [[f"2099-01-02_{wafer:02}", "9", str(wafer), location] + ["opaque-not-a-number"] * 7
            for wafer in (1, 2) for location in locations]
    rows += [["", "", "", ""] + ["opaque"] * 7,
             ["", "9", "3", "F10"] + ["opaque"] * 7,
             rows[0][:]]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)
    canonical = [f"synthetic_channel_{index:02}" for index in range(31)]
    extra = [f"synthetic_extra_{index:02}" for index in range(13)]
    mixed = []
    for index, label in enumerate(canonical):
        mixed.append(label)
        if index < 13:
            mixed.append(extra[index])
    metadata = {"schema_version": "bosch-channel-metadata-v1", "process_file_sha256": "0" * 64,
                "canonical_labels": canonical, "groups": [
                    {"group_path": "/Day_2099_01_02_Wafer_01", "feature_labels": mixed},
                    {"group_path": "/Day_2099_01_02_Wafer_02", "feature_labels": list(reversed(canonical))},
                    {"group_path": "/Day_2099_01_02_Wafer_03", "feature_labels": canonical[:-1] + ["synthetic_replacement"]}]}
    return {"synthetic_inventory.csv": stream.getvalue().encode("utf-8"),
            "synthetic_channels.json": (json.dumps(metadata, indent=2) + "\n").encode("utf-8")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.output_dir.mkdir(mode=0o700, exist_ok=False)
        for name, blob in fixture_bytes().items():
            fd = os.open(args.output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(blob)
    except OSError:
        parser.exit(2, "Cannot create new fixture directory/files; a partial new directory may remain.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
