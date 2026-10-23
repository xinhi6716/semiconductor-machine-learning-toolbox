#!/usr/bin/env python3
"""Run every semiconductor tool's software fixture tests, without dependencies."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SUITES = ("iv-readpoint", "rram-window")


def main():
    failures = []
    for tool in SUITES:
        print("\n=== " + tool + " ===", flush=True)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                                 str(ROOT / "tools" / tool / "tests"), "-v"], cwd=ROOT)
        if result.returncode:
            failures.append(tool)
    print("\n" + ("Failed: " + ", ".join(failures) if failures else "All semiconductor software fixture suites passed."), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
