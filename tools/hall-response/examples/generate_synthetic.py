#!/usr/bin/env python3
"""Print original, invented Hall-response test points; no measured data involved."""
import csv
from decimal import Decimal
import sys


def main():
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("lot", "wafer", "device", "condition", "replicate", "branch",
                     "temperature_K", "bias_current", "magnetic_field", "transverse_voltage"))
    fields = [Decimal(v) for v in ("-1", "-0.5", "0", "0.5", "1")]
    for device, replicate, branch, current, slope, offset, curve in (
            ("invented_A", "r1", "up", 1, 2, "0.3", 0),
            ("invented_A", "r1", "down", 1, 2, "0.4", 0),
            ("invented_A", "r2", "up", -1, -2, "0.3", 0),
            ("invented_B", "r1", "up", 1, 1, "0.1", "0.2")):
        for field in reversed(fields) if branch == "down" else fields:
            voltage = Decimal(slope) * field + Decimal(offset) + Decimal(curve) * field ** 2
            writer.writerow(("invented_lot", "invented_wafer", device, "synthetic_fixed_wiring",
                             replicate, branch, 300, current, field, voltage))


if __name__ == "__main__":
    main()
