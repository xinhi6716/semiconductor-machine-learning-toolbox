#!/usr/bin/env python3
"""Generate original arithmetic FET workflow fixtures, not simulated device physics."""
from pathlib import Path
import sys

import build_features as pipeline


def fixture_payloads():
    rows = []
    # Four namespaced device IDs, two technical repeats, two distinct branches.
    # ID[nA] = lot_multiplier * repeat_multiplier * branch_multiplier * base**step.
    # The bases and multipliers are invented integers, not fitted measurements.
    for lot_number, lot_multiplier in ((1, 1), (2, 5)):
        for device_number, base in ((1, 10), (2, 4)):
            for replicate, repeat_multiplier in (("r1", 1), ("r2", 3)):
                for branch, branch_multiplier, indices in (("forward", 1, range(7)),
                                                           ("reverse", 2, range(6, -1, -1))):
                    for index in indices:
                        rows.append({"lot": f"invented_lot_{lot_number}", "wafer": "invented_wafer_1",
                                     "device": f"invented_device_{device_number}",
                                     "condition": "invented_fixed_protocol", "replicate": replicate,
                                     "branch": branch, "temperature_K": "300", "drain_voltage": "0.05",
                                     "body_voltage": "0", "gate_voltage": f"0.{index}",
                                     "drain_current": str(lot_multiplier * repeat_multiplier * branch_multiplier * base ** index)})
    return {"transfer.csv": pipeline.csv_bytes(pipeline.vth.COLUMNS, rows)}


def main(argv=None):
    parser = pipeline.SafeParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pipeline.write_outputs(args.output_dir, fixture_payloads())
    except (OSError, ValueError):
        print("error: unable to create synthetic inputs; existing destinations are never overwritten", file=sys.stderr)
        return 2
    print("Created an original invented transfer CSV; voltage is V and current is nA. No real data were used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
