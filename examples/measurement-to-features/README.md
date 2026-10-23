# From declared RRAM measurements to device-level features

An **offline, original synthetic example**, not a research dataset or model:

1. Map each acquired I–V branch to an explicitly declared HRS or LRS state,
   device, fabrication batch, wafer, repeated cycle, and read condition.
2. Invoke the existing [I–V readpoint extractor](../../tools/iv-readpoint/README.md)
   at the declared signed read voltage. Preserve its local interpolation bracket.
3. With explicit assumptions, calculate **static terminal resistance**
   `R = V_read / I_read`, requiring a nonzero current of the same sign as voltage.
4. Pass those explicitly labeled resistances to the existing
   [RRAM window evaluator](../../tools/rram-window/README.md).
5. Aggregate the repeated cycles into **one row per declared physical device**,
   preserving provenance and a model-feature allowlist separately from metadata.

This closes a measurement-to-table workflow, not a prediction or causal-inference
workflow. There is no trained model, synthetic prediction target, downloaded
package, network request, external API, instrument connection, or installation
step. The Vth/TLM tools are deliberately not combined with these RRAM measurements:
that would need a separate, scientifically justified physical-unit mapping.

## Run from the repository root

Python 3.10+ source, standard library only. Tested locally on Python 3.12/Linux.
Choose destinations that **do not already exist**; their parents must exist.

```sh
# Regenerate all included fixture bytes from the original deterministic source.
python3 examples/measurement-to-features/generate_synthetic.py \
  --output-dir /tmp/invented-rram-input

# Explicitly agree to the measurement/conversion assumptions described below.
python3 examples/measurement-to-features/build_features.py \
  --input-dir /tmp/invented-rram-input \
  --output-dir /tmp/invented-rram-features \
  --max-bracket-span-V 0.11 --accept-measurement-assumptions

python3 -m unittest discover -s examples/measurement-to-features/tests -v
```

Alternatively use `--input-dir examples/measurement-to-features/fixtures`.
The generator has no random seed, wall-clock input, or machine-specific paths:
it invents 2 batches, 6 devices, 18 repeat cycles, 36 state branches and 108 I–V
points. It defines `I = V*G` from explicitly enumerated integer conductances.
These are arithmetic fixtures, **not simulated switching physics, real laboratory
measurements, validation evidence, or six independently sampled real devices**.
The output has 6 rows, not 18 cycles or 36 independent state samples.

For the first invented device, three HRS conductances are 2, 3, 4 microSiemens;
LRS conductances are 100, 101, 102 microSiemens. Its medians are approximately
333333.333 ohm, 9900.990 ohm, and 33.6667 for the within-cycle HRS/LRS ratio.
Numbers describe software arithmetic only. The ratio median is computed from
individual paired ratios, **not** by dividing two separately aggregated medians.

## Exact input contract

A directory must contain these three UTF-8 CSV files. Headers contain exactly
the listed unique columns, in any order. The example accepts **SI voltage/current
only**: the generic `voltage` and `current` names mirror the existing extractor,
but every value must already be V and A, never mV or microA. No unit inference or
automatic conversion is attempted. The software cannot detect a plausible value
that the operator has mislabeled in the wrong units.

### `iv_sweeps.csv`

```csv
device,cycle,branch,voltage,current
synthetic_device_1_1,001,declared_high_up,0.05,100000e-12
synthetic_device_1_1,001,declared_high_up,0.15,300000e-12
```

Each `(device, cycle, branch)` is exactly one uninterrupted, acquisition-ordered,
monotonic-voltage sweep in a fixed declared state/protocol. Do not concatenate
acquisitions under one key. Interleaved groups are allowed; within-group order is
never sorted or repaired. Duplicate voltage samples are rejected, not deduplicated.

### `manifest.csv`

```csv
batch,wafer,device,cycle,replicate,branch,state,condition
synthetic_batch_1,synthetic_wafer_1,synthetic_device_1_1,001,acquisition_1,declared_high_up,HRS,synthetic_read_v1
synthetic_batch_1,synthetic_wafer_1,synthetic_device_1_1,001,acquisition_1,declared_low_up,LRS,synthetic_read_v1
```

- `device` is a globally unique physical-device ID, not a sweep ID. Its batch,
  wafer and condition must be fixed throughout this input bundle. Reusing a
  local device label across wafers/batches is an error: namespace it at export.
- Exactly one manifest row names each measured `(device, cycle, branch)`. All
  manifest keys and all measured keys must match, with no missing or extra group.
- Exactly one `HRS` branch and one `LRS` branch are required per `(device, cycle)`.
  State is assigned explicitly by the laboratory protocol. Branch names never
  infer it, and a small/large measured resistance never relabels it.
- `cycle` identifies one program/read sequence containing the intended HRS/LRS
  pair. `replicate` identifies that repeat acquisition of the sequence, not a
  second physical device. The HRS/LRS rows must share it; within each device the
  cycle/replicate relationship is one-to-one. It cannot distinguish duplicate
  `(device, cycle, branch)` measurements. Multiple acquisitions of one state
  require an explicitly redesigned sampling/aggregation contract, not new rows
  with an extra replicate label under the same upstream key.
- Cycles are technical repeats within a device. The program checks labels, not
  whether two different device IDs really refer to different physical devices.

### `conditions.csv`

```csv
condition,read_voltage_V,temperature_K,read_delay_ms,protocol
synthetic_read_v1,0.1,300,10,invented_fixed_state_dc_read
```

Each condition ID has one definition. No duplicate, unknown, or unused conditions
are allowed. Read voltage is signed, finite and nonzero; temperature is positive
kelvin; delay is nonnegative milliseconds. Protocol names link to the retained
laboratory record of terminal convention, programming/state preparation, timing,
compliance, calibration, noise floor, and environmental settings. Temperature
and delay are declarations, not measurements checked by the extractor.

Both states and every cycle of a device must use the same condition ID; the
pipeline refuses to aggregate a condition change. Different devices may have
different declared conditions, but that does **not** make their features directly
comparable: partition or account for protocol deliberately downstream. Different
labels are not silently equated even if numeric declarations happen to match.

Identifiers are nonempty, unpadded, at most 128 characters, without ASCII controls
or leading `=`, `+`, `-`, `@` (CSV spreadsheet-formula protection). Numeric fields
accept finite decimal/scientific text, up to 64 characters, 32 coefficient digits,
and decimal exponents from -200 to 200; nonzero binary64 underflow is rejected.
Each input is bounded to 8 MiB and 10,000 records and must be a regular file.
Completely empty physical lines are skipped; empty fields and malformed rows fail.

## Assumptions accepted by the required flag

`--accept-measurement-assumptions` declares that the exporter has:

- Used signed voltage across the measured device and a consistent terminal
  current convention, exported in V and A, with correctly declared device/state
  and condition provenance.
- Identified state-preserving, fixed-protocol read branches, judged the chosen
  read voltage to be appropriate, and checked settling, compliance clipping,
  leakage, noise floor, temperature and read disturb in the actual experiment.
- Accepted local linear current interpolation within the user-supplied positive
  `--max-bracket-span-V` limit, without extrapolation. This spacing bound is not
  an uncertainty estimate or an accuracy claim.
- Chosen to represent the readpoint by positive **static terminal V/I resistance**.
  This is neither differential resistance nor proof of an Ohmic device or
  intrinsic material property. No series/contact resistance is removed. Zero or
  opposite-sign current is rejected; absolute values are never used to rescue it.

The software does not independently verify these physical assumptions. Passing
the checks does not prove that HRS/LRS states were created or remained stable.
Ratios below one are retained, flagged and counted, not inverted or relabeled.

## Outputs and failure behavior

The requested new directory contains:

- `features.csv`: one row per device with batch/wafer/condition, `split_group`
  equal to batch, declared read voltage, repeat count, below-one-ratio count,
  median HRS resistance, median LRS resistance and median paired ratio.
- `feature_schema.json`: exact row key, numeric predictor allowlist, units,
  metadata roles, and an empty target list.
- `iv_readpoints.json`: an example-specific wrapper around the existing
  extractor's group results, source rows, interpolation brackets, assumptions and
  invocation details. Its top-level schema is **not** a drop-in replacement for
  the extractor CLI report. No branch was silently filtered.
- `resistance_input.csv`: the exact derived input sent to the RRAM evaluator.
- `rram_windows.json`: the existing evaluator's full pair and descriptive output.
- `provenance.json`: input/code SHA-256 fingerprints, runtime version, arguments,
  full declared conditions (temperature, delay, protocol and read voltage),
  per-device cycles/replicates, and links from output pairs back to manifest,
  condition and original I–V physical CSV lines (header included).

The pipeline calls `iv_readpoint.load_csv` and `extract_group` from this checkout,
then `rram_window.evaluate_stream`; it does not copy their measurement algorithms.
Input bytes are read once into bounded snapshots, and the same bytes are hashed
and used for extraction. Preserve the original inputs: hashes cannot recover them.

All validation happens before output creation. A missing/rejected/ambiguous
acquisition, duplicate mapping, nonfinite or nonpositive V/I, or condition mismatch
fails the **whole example** with exit `2`: there is no partial feature table,
zero-filling, imputation, hidden deduplication, or silently excluded device.
Exit `0` means software checks completed, not scientific validation. Run the
individual measurement tool on original data for more detailed rejection reports.

Output directories are exclusively created with requested POSIX mode `0700`,
and files with `0600`. Existing files, directories, or symlink destinations are
never overwritten. On an output I/O failure a partially created new directory
may remain; discard it and rerun at a new destination. Other platforms may use
different permission semantics. Input files are unchanged; nothing is sent
anywhere. Stdout/stderr summaries omit IDs and measured values. The deliberately
requested reports do contain them and should not be committed with real data.

## Adapt to a real laboratory without leakage

1. Freeze a documented acquisition protocol and export actual fixed-state I–V
   branches in SI units. Retain unmodified instrument records and all metadata.
2. Build the explicit manifest from sample tracking and state preparation, not by
   guessing HRS/LRS from branch names or ranking the observed resistance.
3. Declare a meaningful signed read voltage and bracket bound. Investigate failed
   groups rather than deleting rows until the tool passes. This example's strict
   complete-pair policy is not a general solution for censored or missing data.
4. Keep the physical device as the minimum row unit. Use the experiment's true
   dependency structure when evaluating models: device, wafer, batch, time, or
   fabrication campaign may be the grouping boundary. `split_group=batch` is
   a conservative example declaration, **not** proof that batches are independent.
5. Use only the documented feature allowlist as candidate predictors, not IDs,
   counts, or metadata by accident. The three features are related: the window
   ratio comes from the same HRS/LRS observations. Do not present predicting that
   ratio from those resistances as a scientific prediction task.
6. A real target must come from a separately defined endpoint with known timing
   and provenance. Features must be available before that endpoint; post-outcome
   reads, target-derived state labels, or future cycles leak information. Split
   independent groups before fitting imputation, scaling or feature selection.
   Nothing here automatically checks endpoint timing or makes a causal claim.

The synthetic six-device table is too small and contrived for a meaningful model
assessment. Feed a reviewed real table into a separately installed downstream ML
workflow only after defining the endpoint, grouped validation and sample-size
limitations. No ML Super Calculator installation or runtime dependency is hidden
in this example, and no external calculator compatibility is asserted or required.

## Verification scope

The isolated tests cover deterministic fixture regeneration, actual calls to both
measurement tools, counts and medians, line/hash provenance, join bijections,
missing/extra/duplicate acquisitions, condition and replicate misalignment,
negative signed reads, invalid conversions, no extrapolation, formula-safe IDs,
all-or-nothing validation and private/no-overwrite output. Existing measurement
suites remain necessary and are run separately with `python3 run_tests.py`.
Passing synthetic software tests is not experimental, metrological or scientific
validation. The source, fixture generator, fixture data and tests are original
and covered by the repository's [MIT license](../../LICENSE).
