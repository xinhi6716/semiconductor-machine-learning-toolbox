#!/usr/bin/env python3
"""Emit original invented arithmetic fixtures, not simulations or measurements."""
import csv
import sys


def main():
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("lot", "wafer", "device", "condition", "replicate", "branch",
                     "temperature_K", "drain_voltage", "body_voltage", "gate_voltage", "drain_current"))
    gates = ("0", "0.1", "0.2", "0.3", "0.4", "0.5")
    groups = (
        ("invented_A", "forward", gates, ("0.01", "0.1", "1", "10", "100", "1000")),
        ("invented_A", "reverse", gates[::-1], ("100", "10", "1", "0.1", "0.01", "0.001")),
        ("invented_B", "curved", gates, ("0.01", "0.1", "2", "10", "100", "1000")),
        ("invented_C", "negative_slope", gates, ("1000", "100", "10", "1", "0.1", "0.01")),
    )
    for device, branch, xs, ys in groups:
        for gate, current in zip(xs, ys):
            writer.writerow(("synthetic_lot", "synthetic_wafer", device, "invented_protocol",
                             "r1", branch, "300", "0.1", "0", gate, current))


if __name__ == "__main__":
    main()
