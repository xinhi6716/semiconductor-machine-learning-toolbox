#!/usr/bin/env python3
"""Print original invented coordinate fixtures, not measurements or simulations."""
import argparse
import csv
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=("hall", "gate"))
    args = parser.parse_args()
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("lot", "wafer", "device", "condition", "sequence", "sample_id", "coordinate"))
    if args.example == "hall":
        # 281 supplied samples, 280 edges, one shared peak observation.
        coordinates = list(range(-70, 71)) + list(range(69, -71, -1))
        condition = "invented_field_axis_mT"
    else:
        # A plateau reversal, a plateau without reversal, and a final up run.
        coordinates = ("0", "0.5", "1", "1.0", "1e0", "0.5", "0", "0.0", "-0.5", "0")
        condition = "invented_source_referenced_gate_V"
    for index, coordinate in enumerate(coordinates, 1):
        writer.writerow(("invented_lot", "invented_wafer", "invented_device", condition,
                         "supplied_sequence_A", f"sample_{index:03d}", coordinate))


if __name__ == "__main__":
    main()
