#!/usr/bin/env python3
"""Generate original deterministic invented RRAM read sweeps, never lab data."""
from fractions import Fraction
from pathlib import Path
import sys

from build_features import (CONDITION_COLUMNS, IV_COLUMNS, MANIFEST_COLUMNS,
                            SafeParser, csv_text, write_outputs)


def fixture_payloads():
    manifest, sweeps = [], []
    conditions = [{"condition": "synthetic_read_v1", "read_voltage_V": "0.1",
                   "temperature_K": "300", "read_delay_ms": "10",
                   "protocol": "invented_fixed_state_dc_read"}]
    # Six invented devices, three repeat program/read cycles each. Integer
    # conductances make the fixture definition transparent and deterministic.
    # I = V*G is a software arithmetic fixture, not a fitted switching model.
    for batch_index in range(2):
        for device_index in range(3):
            batch = f"synthetic_batch_{batch_index + 1}"
            wafer = f"synthetic_wafer_{batch_index + 1}"
            device = f"synthetic_device_{batch_index + 1}_{device_index + 1}"
            for cycle_index in range(3):
                cycle = f"{cycle_index + 1:03d}"
                replicate = f"acquisition_{cycle_index + 1}"
                for state, branch, base in (("HRS", "declared_high_up", 2), ("LRS", "declared_low_up", 100)):
                    manifest.append({"batch": batch, "wafer": wafer, "device": device,
                                     "cycle": cycle, "replicate": replicate, "branch": branch,
                                     "state": state, "condition": "synthetic_read_v1"})
                    conductance_uS = base + 2 * batch_index + device_index + cycle_index
                    for voltage in (Fraction(5, 100), Fraction(15, 100), Fraction(25, 100)):
                        # Every rational here has an exact finite decimal.
                        current_pA = int(voltage * conductance_uS * 1000000)
                        sweeps.append({"device": device, "cycle": cycle, "branch": branch,
                                       "voltage": f"{float(voltage):.2f}", "current": f"{current_pA}e-12"})
    return {"manifest.csv": csv_text(MANIFEST_COLUMNS, manifest),
            "conditions.csv": csv_text(CONDITION_COLUMNS, conditions),
            "iv_sweeps.csv": csv_text(IV_COLUMNS, sweeps)}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory only")
    args = parser.parse_args(argv)
    try:
        write_outputs(args.output_dir, fixture_payloads())
    except (OSError, UnicodeError, ValueError):
        print("error: unable to create synthetic fixtures; existing destinations are never overwritten", file=sys.stderr)
        return 2
    print("Created invented deterministic fixtures, not research measurements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
