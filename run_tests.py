#!/usr/bin/env python3
"""Run semiconductor tool and end-to-end example fixture tests, without dependencies."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SUITES = ("iv-readpoint", "rram-window", "tlm-extract", "vth-constant-current", "hall-response")


def main():
    failures = []
    suites = [(tool, ROOT / "tools" / tool / "tests") for tool in SUITES]
    suites.append(("measurement-to-features", ROOT / "examples" / "measurement-to-features" / "tests"))
    for tool, tests in suites:
        print("\n=== " + tool + " ===", flush=True)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                                 str(tests), "-v"], cwd=ROOT)
        if result.returncode:
            failures.append(tool)
    print("\n" + ("Failed: " + ", ".join(failures) if failures else "All semiconductor software fixture suites passed."), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
