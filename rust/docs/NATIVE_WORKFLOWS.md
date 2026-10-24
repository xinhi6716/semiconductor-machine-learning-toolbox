# Native measurement-to-feature workflows

The `smlt` executable includes both original end-to-end feature workflows as
native Rust commands. They call the existing Rust extraction modules in-process.
They do not launch Python, a subprocess, a network request, an instrument driver,
or an ML model. The checked-in inputs are invented arithmetic fixtures, not real
measurements or physical validation.

## RRAM: declared measurements to device-level features

Run from the extracted distribution directory. Choose a new output path whose
parent already exists:

```sh
./smlt rram-features \
  --input-dir examples/measurement-to-features/fixtures \
  --output-dir /tmp/invented-rram-features \
  --max-bracket-span-V 0.11 \
  --accept-measurement-assumptions
```

For a source build, use `target/release/smlt` in place of `./smlt`. On Windows,
use `smlt.exe` and an appropriate new output path.

The three input files are:

```text
manifest.csv:
batch,wafer,device,cycle,replicate,branch,state,condition

conditions.csv:
condition,read_voltage_V,temperature_K,read_delay_ms,protocol

iv_sweeps.csv:
device,cycle,branch,voltage,current
```

Headers contain exactly these unique columns, in any order. I–V voltages and
currents are already SI V/A; no units are inferred. The full original input
contract is retained: bounded regular UTF-8 CSV files, at most 8 MiB and 10,000
records each; exact declared measurement keys; one explicitly labeled HRS and LRS
branch per device/cycle; matching replicate declarations; no unknown, duplicate,
or unused conditions. Device is a globally unique declared physical-device ID,
and its batch, wafer and condition may not change across cycles.

All identifiers are nonempty, unpadded, ASCII-control-free, at most 128 Unicode
characters and may not start with `=`, `+`, `-`, or `@`. Decimal numeric text is
bounded to 64 stripped characters, 32 coefficient digits and a stored decimal
exponent of magnitude at most 200. Nonfinite values and nonzero binary64
underflow are rejected. Temperature must be positive, delay nonnegative, and
read voltage nonzero.

The required flag declares state-preserving fixed-protocol signed-terminal
measurements, appropriate read bias, reviewed settling/compliance/noise/state
assumptions, and acceptance of local linear-current interpolation within the
explicit positive voltage-spacing bound. Static terminal resistance is signed
`R = V / I`, requiring positive finite resistance and matching nonzero signs.
No absolute-value repair, inferred state, extrapolation, contact correction or
claim of Ohmic physics is made.

The native workflow loads one private byte-for-byte I–V snapshot, calls
`iv_readpoint::load_csv` once and `extract_group` at each declared read voltage,
then feeds its derived resistance CSV to `rram_window::run`. It never reimplements
those scientific algorithms. Manifest ordering does not alter feature ordering.

Success emits six files:

- `features.csv`: one row per declared device, with metadata and within-device
  medians of HRS resistance, LRS resistance and the individual paired ratios.
- `feature_schema.json`: the unchanged seven-field version-1 candidate-predictor
  allowlist, metadata, units, empty targets and scientific warnings.
- `iv_readpoints.json`: complete group extractions in the original workflow's
  example-specific wrapper, with original physical source-line numbers.
- `resistance_input.csv`: the exact derived CSV passed to the native RRAM module.
- `rram_windows.json`: the full RRAM pair and descriptive-summary report.
- `provenance.json`: input hashes, native source fingerprints, conditions,
  acquisition lineages and device cycle/replicate mappings.

The fixture produces six device rows, not eighteen independent cycles. Ratios
below one are retained and counted, not inverted. The ratio median is calculated
from within-cycle ratios, not from separately aggregated resistance medians.

Exit `0` means a complete software export. Any invalid input, missing/extra join,
failed readpoint, invalid resistance or excluded pair is fatal with exit `2`,
before the output directory is created. There is no partial-cohort feature export.

## FET: one shared transfer input to separate branch features

```sh
./smlt fet-features \
  examples/fet-measurement-to-features/fixtures/transfer.csv \
  --output-dir /tmp/invented-fet-features \
  --voltage-unit V --current-unit nA \
  --criterion-current 50 --criterion-current-unit nA \
  --current-polarity positive --max-bracket-span 0.1 \
  --window-min 0.1 --window-max 0.4 \
  --accept-measurement-assumptions
```

The exact input columns are:

```text
lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current
```

All six identifier columns form an acquisition-ordered branch key. Physical
device identity is the full `(lot, wafer, device)` tuple, never device text alone.
Interleaved groups preserve their original within-branch order. Repeated branches
are not independent devices and are not silently merged.

Voltage units (`V`, `mV`, `uV`) apply to gate/drain/body voltage, bracket span and
inclusive window bounds. Current and criterion-current units are independently
explicit (`A`, `mA`, `uA`, `nA`, `pA`). Current polarity is `positive` or `negative`;
a negative declared criterion remains signed, for example
`--criterion-current=-50 --current-polarity negative`.

All existing native core contracts apply, including 8 MiB/10,000-row input
bounds, 1,000 groups and 1,000 samples per group; bounded exact decimal parsing;
and nonzero SI magnitudes from `1e-150` through `1e150`. The assumption flag accepts
both linked extraction methods' fixed-state, fixed-temperature/bias,
source-referenced terminal, polarity, criterion/window and local-interpolation
assumptions. It does not verify them experimentally or identify a physical regime.

Both native core modules consume exactly the same private retained snapshot.
Every report input hash, six-part branch key, count, source-row list and successful
branch's fixed conditions must match before the workflow accepts the join.

Exit `0` creates eight files:

- `features.csv`: complete metadata plus signed criterion VGS and log-current
  slope, one row per acquisition branch.
- `predictors.csv`: exactly those two allowlisted numeric columns, in the exact
  same row order as `features.csv`.
- `feature_schema.json`: the unchanged exact seven-field version-1 allowlist.
- `vth_report.json` and `log_slope_report.json`: unchanged complete native core
  report objects, including observations, windows, rejection reasons and lineage.
- `transfer_input.csv`: original input bytes, including BOM/header order/numeric
  text if present, unchanged and shared by both extractors.
- `workflow_status.json`: both per-branch statuses and complete/audit-only status.
- `provenance.json`: hashes, native runtime/source identity, physical-device counts,
  explicit parameters and feature/predictor/original-row alignment.

If any branch fails either extractor, exit `1` creates only five audit files:
both full reports, the retained input, workflow status and provenance. It emits
no `features.csv`, `predictors.csv` or `feature_schema.json`, even if other branches
pass. All feature/predictor lineage row references are null. Exit `2` denotes a
fatal argument, malformed-input, unsafe-CSV-ID, integrity or I/O error.

The original fixture has sixteen branches from four namespaced declared physical
devices. Its two local device labels intentionally repeat across lots. A valid
zero or negative log slope is preserved rather than replaced with missing/absolute
values. Both features come from the same acquisition; neither is automatically a
valid predictor for a later endpoint. Do not predict a target from itself or its
algebraic proxy. No target, model or accuracy score is supplied.

## Output safety and retained privacy contract

- Validation and all scientific calculations finish in memory before creating a
  requested output directory.
- Directories are exclusively created with requested POSIX mode `0700`; files
  use exclusive creation with mode `0600`. Other platforms have different
  permission semantics, so use an appropriately protected destination. Windows
  uses inherited ACLs, not a promised owner-only permission policy. A successful
  Windows cross-build is not evidence of Windows runtime validation.
- Existing files, directories and live or dangling symlink destinations are never
  overwritten. The workflow does not merge, rename over or delete destinations.
- POSIX output writes pin the new directory with a directory file descriptor and
  use `openat` with exclusive/no-follow flags.
- Output I/O failure can leave a partially created new directory, as in the
  reference implementation. Inspect it and select a new destination when retrying.
- Temporary snapshots are private and cleaned up on ordinary return or error.
  Abrupt process termination can leave a temporary snapshot; this is not a secure
  erase guarantee.
- Summaries omit identifiers, values and input/output paths. The deliberately
  requested reports and snapshots do contain source data. Do not publish real
  measurements or commit those bundles accidentally.

## Explicit native provenance differences

Scientific schemas remain unchanged. Only each workflow's `provenance.json`
changes from schema version `1` to `2`:

1. Python runtime fields are replaced by a native Rust runtime identity and
   package version. No Python execution is claimed.
2. RRAM `code_sha256` and FET `source_sha256` identify the compile-time embedded
   `src/workflows.rs`, the invoked native core modules, `src/common.rs`,
   `src/reference_constants.json` and `Cargo.lock`. They are hashes of compiled
   source snapshots, not runtime reads of mutable source files. A source edit
   after building does not change the executable's recorded provenance.
3. RRAM `iv_readpoints.json.invocation` and FET provenance invocation descriptions
   name the actual Rust calls. The native binary has no dynamically loaded Python
   modules, so a Python-only post-import source-change guard has no equivalent
   mutable runtime module to monitor.
4. JSON whitespace, key ordering and numeric exponent spelling may differ from
   Python. FET output hashes are calculated from the exact emitted native bytes,
   so they correctly differ when serialization or provenance differs.

`feature_schema.json` remains version 1 with exactly seven keys. Neither core
report's schema is relabeled. All scientific values, branch statuses, grouping,
allowlists, row alignment and source-row lineage are compared without numerical
tolerance against the unchanged reference in development tests.

## Verification and its boundaries

Development-only differential checks run the actual native executable while
separately loading the unchanged Python reference as an oracle:

```sh
SMLT_BINARY="$PWD/target/debug/smlt" \
SMLT_PYTHON_REFERENCE=/path/to/unchanged/python-reference \
  python3 tests/workflow_cli_parity.py

cargo test --offline workflows::tests
```

Python is required only for that optional comparison test, not for either product
command. Native subprocesses in the suite run with an empty `PATH`. CSV outputs
are compared byte-for-byte; parsed scientific JSON is compared exactly. Signed zero is compared explicitly rather than treated as equal to positive zero.
Only the explicit provenance differences above are normalized, and native source/output
hashes are independently recomputed and verified.

The CLI suite covers original fixtures, signs, exact window boundaries, unit
conversion, multiple read conditions, physical-device and tuple identity,
ordering, counts/lineage, complete and audit-only gates, malformed/nonnumeric/
oversized/special files, private diagnostics, permissions and no-overwrite output.
Native unit tests additionally inject corrupt report keys, hashes, schemas,
counts, source rows and conditions; verify snapshot survival after original-source
mutation; and check cleanup and retained partial outputs on I/O failure.

These are native software tests, not the original Python API suite relabeled as
Rust passes. Python monkeypatch call counts, dynamic import/source mutation, and
the Python fixture-generator implementation are not native runtime APIs. Their
relevant observable contracts are exercised by native CLI or unit tests instead.
Synthetic parity is not experimental, metrological or scientific validation.
