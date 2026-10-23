#!/usr/bin/env python3
"""Run semiconductor tool and end-to-end example fixture tests, without dependencies."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SUITES = ("iv-readpoint", "rram-window", "tlm-extract", "vth-constant-current", "hall-response", "sweep-segments", "transfer-log-slope")


def main():
    failures = []
    suites = [(tool, ROOT / "tools" / tool / "tests") for tool in SUITES]
    for example in ("measurement-to-features", "fet-measurement-to-features"):
        suites.append((example, ROOT / "examples" / example / "tests"))
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
