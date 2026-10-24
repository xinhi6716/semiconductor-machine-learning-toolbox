# Independent native runtime checks

These are separate original synthetic/adversarial checks, not a count of the reference test harness. The production executable never calls these Python scripts.

Requirements: Python 3.10+, the unchanged Python reference tree, a native Linux executable, and the matching Rust source snapshot. Run from the Rust source package root. Defaults use bundled `reference/python`, `target/release/smlt`, and `validation/independent`.

Configuration uses environment variables:

- `SMLT_BINARY`: absolute path to the exact immutable native executable being reviewed.
- `SMLT_PROJECT_ROOT`: source package root, default current working directory.
- `SMLT_PYTHON_REFERENCE`: unchanged reference root, default `reference/python` beneath the project root.
- `SMLT_SOURCE_ROOT`: source snapshot whose hashes were embedded in workflow provenance, default project root.
- `SMLT_REVIEW_OUTPUT`: generated fixture/report directory, default `validation/independent` beneath the project root.

Run:

```sh
python3 tests/independent/review_cli.py
python3 tests/independent/review_safety.py
python3 tests/independent/review_workflows.py
```

Keep the three scripts together. Every run hashes the tested executable. Generated input is deterministic and synthetic, with no network requests. Runtime comparisons execute the actual Python and Rust CLIs, not a rewritten surrogate calculation.

- `review_cli.py`: 464 cases across the ten core commands. Exact scientific JSON and finite binary64 values, including signed zero, are compared without tolerance. Deliberate Hall/TLM RMSE midpoint cases, exact decimal decisions, opaque response bytes, constructor limits, real report caps and valid exact-input-size boundaries are included.
- `review_safety.py`: 194 checks of explicit stdout, no overwrite, private POSIX modes, symlinks, FIFOs, categorical errors, invalid Unicode arguments, unchanged input and early resource rejection. This script is Linux/POSIX-specific and uses a child-only 256-MiB address-space cap for malformed-input tests.
- `review_workflows.py`: 68 actual-CLI workflow cases, comparing exact scientific JSON and CSV bytes. Only declared native schema-2 provenance fields differ from Python; source/output fingerprints are separately verified. It also checks native execution with an empty PATH, temporary snapshot cleanup, audit-only suppression, unchanged inputs and exclusive output directories.

These tests do not establish physical validity, Windows runtime compatibility, or complete coverage of all possible inputs. A source or executable change requires a new run. Do not add overlapping counts together as though they were distinct unit tests.
