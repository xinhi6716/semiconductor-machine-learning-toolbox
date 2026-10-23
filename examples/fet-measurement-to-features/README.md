# Shared FET measurements to separate branch features

An offline, original synthetic workflow that runs the existing
[constant-current Vth extractor](../../tools/vth-constant-current/README.md) and
[user-windowed transfer log-slope tool](../../tools/transfer-log-slope/README.md)
on **the same exact transfer-CSV bytes**. It exports one row per declared branch,
not one independent sample per row, and retains both complete tool reports.
It does not copy or change either extraction algorithm.

The two candidate features are:

- `criterion_gate_source_voltage_V`: signed VGS at the explicitly supplied signed
  terminal-current criterion, using an exact measured hit or adjacent **linear
  current** interpolation. It is an operational criterion value, not a universal
  physical threshold.
- `log_current_slope_decades_per_V`: signed descriptive least-squares slope of
  `log10(s * ID_A / 1 A)` versus signed VGS, using all measured samples inside an
  explicitly chosen inclusive VGS window. `s` follows the declared current sign.
  Neither this slope nor its reciprocal is a certified subthreshold swing.

No physical regime, device type, state, window, current criterion, geometry
normalization, target, or model is inferred. This is an arithmetic/data-lineage
example, not simulated transistor physics, experimental evidence, ML performance
or an endorsement of the fixture's criterion/window for real devices.

## Run from the repository root

Python 3.10+; only the standard library and these two existing tools. There is no
network, dependency installation, external API, instrument control, upload, or
model fit. The chosen output directory must not exist, and its parent must exist.

```sh
RUN_DIR="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="fet-workflow-"))')"
export RUN_DIR
python3 examples/fet-measurement-to-features/generate_synthetic.py \
  --output-dir "$RUN_DIR/invented-input"

python3 examples/fet-measurement-to-features/build_features.py \
  "$RUN_DIR/invented-input/transfer.csv" \
  --voltage-unit V --current-unit nA \
  --criterion-current 50 --criterion-current-unit nA \
  --current-polarity positive --max-bracket-span 0.1 \
  --window-min 0.1 --window-max 0.4 \
  --accept-measurement-assumptions --output-dir "$RUN_DIR/branch-features"

python3 -m unittest discover -s examples/fet-measurement-to-features/tests -v
```

The checked-in `fixtures/transfer.csv` is an interchangeable input. The generator
reproduces it byte-for-byte, without randomness, timestamps or external sources.
It invents four namespaced physical-device labels across two lots, two technical
replicates and two branches per device: **16 branch rows from 112 observations**.
Local device names intentionally repeat across lots. Identity is the full
`(lot, wafer, device)`, not the device text alone.

The explicit arithmetic is `ID[nA] = L * R * B * base**step`, with `L` equal to
1 or 5 for the two lots, `R` equal to 1 or 3 for the two replicates, `B` equal to
1 or 2 for forward/reverse branches, and `base` equal to 10 or 4 for the two local
device labels. VGS is `step / 10 V`, for integer steps 0 through 6; reverse
branches are stored from step 6 to 0. This is invented arithmetic, not a fitted
or physically validated device model. The forward and reverse multipliers are
not evidence of a hysteresis mechanism.

At the documented settings the first forward/reverse pair gives criterion VGS
approximately `0.1444444444 V` and `0.1166666667 V`, respectively, with `0.1 V`
interpolation brackets. Both log slopes are `10 decades/V`. The base-4 devices
give approximately `6.020599913 decades/V`. Four points per branch are in the
window: 64 fitted rows and 48 outside-window rows retained in the audits.
These numbers test software, not real-device accuracy or scientific validity.

## Exact input and parameter contract

Both tools accept the same UTF-8 CSV schema, with exactly these unique columns,
in any order. No manifest join or cross-device matching is invented:

```csv
lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current
```

- All six ID columns together identify one acquisition-ordered branch. Preserve
  branches, repeats, lots and wafers; do not concatenate acquisitions under one
  key. Interleaving groups is allowed, but within-group order is never sorted.
  Conditions may differ between groups; results are not pooled or made comparable
  just because they share a device. The condition ID must link to a separately
  retained acquisition protocol, including timing and preconditioning.
- Voltage columns mean source-referenced VDS, VBS and VGS. `--voltage-unit` is
  required (`V`, `mV`, `uV`) and applies to all three columns, the positive maximum
  VGS bracket span, and both inclusive window bounds. Kelvin temperature is
  fixed and positive; VDS is fixed and nonzero and VBS fixed within each branch.
- `--current-unit` and `--criterion-current-unit` are independently required
  (`A`, `mA`, `uA`, `nA`, `pA`). Currents are signed, unnormalized terminal currents,
  not density or W/L-normalized values. `--current-polarity positive|negative`
  declares the sign convention, not n-/p-channel type. Negative current requires
  a negative criterion, such as `--criterion-current=-50` with unit `nA`.
- There is no default criterion, window, branch filter or bracket width. A single
  criterion and window apply to every branch in a run. Different required
  criteria/windows need separately documented runs; do not tune them to a target
  or to obtain attractive results. The spacing bound is not uncertainty.
- IDs must satisfy the tools' nonempty, unpadded, control-free, 128-character
  limit. For CSV feature export, IDs beginning with `=`, `+`, `-`, or `@` are also
  rejected rather than escaped or renamed, to avoid spreadsheet formulas.
  The exact input snapshot and JSON audits are not spreadsheet-sanitized.
- All existing numeric/resource contracts apply: decimal/scientific text with
  at most 64 stripped characters, 32 coefficient digits and exponent magnitude
  at most 200; nonzero SI inputs from `1e-150` through `1e150`; maximum 8 MiB,
  10,000 rows, 1,000 groups and 1,000 rows per group. Malformed values anywhere,
  even outside the chosen window, fail. Only regular files are read.
- Blank physical lines are skipped, not malformed nonempty rows. UTF-8 BOM and
  quoted CSV are supported. Source rows are physical line numbers at the end of
  each record, including the header. The original bytes are retained unchanged.

The required `--accept-measurement-assumptions` declares acceptance of **both**
linked tools' assumption sets: source-referenced signed terminals; fixed device
state, temperature, bias and acquisition protocol; correctly declared polarity;
justified criterion, explicit window and local linear-current interpolation;
and independent review of settling, noise floor, leakage, compliance, contacts
and physical interpretation. Software cannot verify those declarations or infer
linear/saturation/subthreshold regimes. No current sign is rescued by absolute
value, no points trimmed, no branch merged, and no geometry correction performed.

The joint workflow is deliberately stricter than either tool alone. For example,
a wrong-sign sample outside the log window can be retained with a warning by the
log tool but rejected by the Vth tool. A valid zero log slope is exported as zero,
not missing; its undefined reciprocal remains null in the full log report.

## Outputs, failures and audit

Success creates eight files:

- `features.csv`: one row per six-part branch key, retaining device/condition/
  replicate/branch IDs, lot-based `split_group`, fixed conditions, criterion,
  window, bracket limit, counts, extraction method and the two candidate features.
- `predictors.csv`: **exactly the two allowlisted numeric columns**, in exactly
  the same data-row order as `features.csv`. It has no IDs, target or metadata.
  Do not shuffle or separately filter it: use the full table for identity joins
  and grouping, and `provenance.json` for its row-to-acquisition mapping.
- `feature_schema.json`: the exact seven-field version-1 calculator allowlist:
  `schema_version`, `row_key`, `model_feature_allowlist`, `metadata_columns`,
  `target_columns`, `feature_units`, `warnings`. Targets are deliberately empty.
- `vth_report.json` and `log_slope_report.json`: complete upstream report objects,
  with their original schema, parameters, observations, warnings, source rows,
  interpolation brackets, window decisions, residuals and signed reciprocals.
  No wrapper silently changes their top-level schema; JSON formatting may differ.
- `transfer_input.csv`: the exact bounded input snapshot used and hashed by both
  extractors, including original numeric text, header order and BOM if present.
- `workflow_status.json`: complete or audit-only status plus both per-branch
  statuses/rejection reasons. Successful extraction does not certify ML suitability.
- `provenance.json`: input, source-code and other-output SHA-256 fingerprints,
  runtime and exact invocation parameters, counts, physical-device key, and
  original-source/feature/predictor row lineage. Keep the whole output bundle.

If **any branch is rejected by either extractor**, exit is `1` and the requested
new directory contains only the five audit files: both full reports, input
snapshot, workflow status and provenance. No feature table, predictor CSV or
allowlist is emitted, even for other passing branches. Both statuses remain
visible; no incomplete branch is silently filtered or imputed. Investigate the
records and protocol rather than deleting failed groups until export succeeds.
This fail-closed policy is not a general missing-data or censoring methodology.

Exit `0` means a complete software export, not physical or ML validation.
Exit `2` means a fatal CLI, malformed-input, unsupported schema, join-integrity,
unsafe-CSV-ID or I/O error. Validation precedes output creation; a fatal parsing
error has no recoverable upstream report and creates no output directory. An
output I/O failure can leave an incomplete new directory: discard it and use a
new destination after investigating the cause. Existing files/directories and
live or dangling symlink destinations are never overwritten. Output directories
request POSIX mode `0700`, files `0600`; other platforms may differ. Diagnostics
omit private IDs/values/paths. Deliberately requested files contain raw data and
must not be committed with real measurements. Nothing is transmitted externally.

## Calculator handoff without target leakage

The generated `feature_schema.json` is structurally compatible with the actual
ML Super Calculator / `groupbench` `--features-file` parser. That parser reads
column names, **not scientific validity, endpoint timing or causal eligibility**.
Its exact local parser was exercised without fitting a model; no calculator is
required to run this workflow. A calculator installation may have its own
numerical dependencies; this example neither installs nor imports them.

If you already have a local calculator checkout/environment, independently check
only the allowlist (substitute its actual `src` directory):

```sh
PYTHONPATH=/path/to/ml-super-calculator/src python3 - <<'PY'
import os
from pathlib import Path
from groupbench.feature_manifest import read_feature_manifest
path = Path(os.environ["RUN_DIR"]) / "branch-features/feature_schema.json"
assert read_feature_manifest(path) == [
    "criterion_gate_source_voltage_V", "log_current_slope_decades_per_V"]
print("Feature-name parser compatibility passed; no target or model was created.")
PY
```

For a real downstream task:

1. Define an independently sourced endpoint and when it becomes available, before
   creating model inputs. There is no fabricated target or executable model-run
   example here. Both features share one acquisition, so related-measurement or
   post-outcome leakage must be considered even without an algebraic identity.
2. **Never use the target itself or its algebraic proxy as a feature.** If Vth is
   the target, this same criterion VGS cannot be a predictor. If log slope is the
   target, its reciprocal or reciprocal magnitude leaks the answer. Both features
   are only candidates: revise the allowlist for the actual task. The schema
   excludes reciprocals and all diagnostic/metadata columns by default.
3. Preserve the actual dependency structure. The minimum physical-device key is
   `(lot, wafer, device)`; all branches, repeats and conditions of a device must
   stay in the same evaluation group. `split_group=lot` is a conservative example
   declaration, not proof of independent lots. Wafer, campaign or time dependencies
   may require a different, stricter grouping. Never randomly split branch rows.
4. Keep IDs and grouping metadata outside predictors. Use the full table for a
   reviewed one-to-one target join and grouping; the predictor-only CSV cannot
   reconstruct identities by itself. Fit preprocessing only on training groups.
5. Four invented devices, sixteen branches and two invented lots are **not** a
   meaningful sample for model evaluation or independent-device statistics.
   No score, fitted model, causal claim or real-device performance is reported.

## Verification scope

Run the focused command above and `python3 run_tests.py`. Regression tests cover
both actual tool calls, exact full-report retention, fixture regeneration,
branch/device identity, signs and units, exact decimal window selection, valid
zero slope, both one-tool-only rejection directions, fail-closed export,
source/output hashes, original-byte snapshot consistency, source-row lineage,
calculator schema shape, private diagnostics and no-overwrite outputs. Synthetic
software tests do not validate an instrument or a physical regime. These original
source files and fixtures use the repository's [MIT license](../../LICENSE).
