# Semiconductor Machine Learning Toolbox

## Native Rust downloads

The additive [native Rust edition](rust/README.md) provides the same ten core
commands plus two end-to-end feature workflows without a Python runtime. Start
with the [Traditional Chinese Quickstart](rust/QUICKSTART_zh-TW.md),
[validation and platform limits](rust/VALIDATION.md), or the
[Windows/Linux/source release](https://github.com/xinhi6716/semiconductor-machine-learning-toolbox/releases/tag/rust-v0.1.0).
Linux is runtime-tested; Windows is cross-compiled and inspected, not yet
Windows-runtime-tested. The original Python tools and usage below are unchanged.

Small, inspectable tools for **semiconductor measurement preprocessing and
ML-ready feature extraction**. This repository is semiconductor-only: generic
CSV utilities, experiment runners, and unrelated ML demos do not belong here.

The usable core covers semiconductor I–V measurements, resistive RAM
(RRAM/ReRAM), conventional linear transfer-length-method (TLM) contact
measurements, constant-current transfer-sweep criteria, and Hall-response
diagnostics, plus an ordered sweep-coordinate audit, user-windowed transfer
log-current fitting, and an ordered I–V signed-path integral. A ninth tool audits
BOSCH plasma-etch identity and channel-name metadata. A tenth compares exact
voltage-grid coverage across declared I–V sweeps without inspecting response
values. All ten command-line tools use only the Python standard library and preserve
provenance. Measurement-reading tools require explicit units; the metadata-only
BOSCH audit neither requires nor verifies physical units. They make validation
limits explicit instead of silently
repairing questionable measurements.

**Current scope:** feature preprocessing and descriptive measurement summaries.
There is no trained ML model, device simulator, instrument driver, automatic
switching-state classifier, or claim of experimentally validated performance.
All included measurements are invented software-test fixtures.

## Included tools

1. **[I–V Readpoint Extractor](tools/iv-readpoint/README.md)**
   Extract signed current and static `I/V` at a specified read voltage within each
   explicit device/cycle/monotonic branch. Preserve hysteresis branches; reject
   duplicated voltages and reversals; interpolate only between adjacent samples;
   never extrapolate. Optional bracket-span limits help control interpolation
   distance, without claiming physical accuracy.
2. **[RRAM Resistance-Window Evaluator](tools/rram-window/README.md)**
   Pair explicit HRS/LRS resistance observations for the same device and cycle,
   check declared read-condition IDs and signed nonzero read voltages, and compute
   `R_HRS / R_LRS`. Report missing pairs, invalid resistance values, and condition
   mismatches. Summarize repeated cycles within each device and condition, not as
   independent devices or population-level evidence.
3. **[Linear TLM Extractor](tools/tlm-extract/README.md)**
   Fit total pair resistance versus edge-to-edge contact gap within explicit
   lot/wafer/structure/condition/replicate groups. Calculate channel sheet
   resistance and per-contact resistance only under declared equal-contact,
   uniform-width linear-TLM assumptions. Retain negative fit coefficients as
   rejected fits, expose residuals and extrapolation warnings, and never infer
   contact resistivity, transfer length or independent-device statistics.
4. **[Constant-Current Threshold Criterion Extractor](tools/vth-constant-current/README.md)**
   Find signed VGS at an explicitly declared signed drain-current criterion,
   keeping lot/wafer/device/condition/replicate/branch provenance and fixed
   temperature/VDS/VBS declarations. Preserve forward/reverse sweeps separately;
   reject ambiguous crossings and nonmonotonic sweeps; interpolate only inside
   adjacent measured brackets. No automatic W/L normalization, extrapolation,
   universal physical threshold or causal interpretation is supplied.
5. **[Hall Response Extractor](tools/hall-response/README.md)**
   Fit signed transverse voltage versus magnetic flux density within explicit
   physical-device/condition/replicate/branch groups at fixed declared current
   and temperature. Report signed slope, zero-field intercept, residuals and
   current-normalized apparent sensitivity only for nonzero current. No branch
   merging, automatic offset cancellation or carrier-property inference.
6. **[Ordered Sweep Segment Audit](tools/sweep-segments/README.md)**
   Map supplied voltage or magnetic-flux-density coordinates into maximal exact
   increasing/decreasing/flat edge runs. Retain every observation and explicit
   shared-endpoint identities, including plateau turnarounds. No inferred
   acquisition chronology, cycles, independent observations or physical hysteresis;
   no automatic branch export or handoff to the extractors.

7. **[User-Windowed Transfer Log-Slope](tools/transfer-log-slope/README.md)**
   Fit log10 of explicitly declared-polarity drain current versus VGS over one
   user-supplied voltage window, separately by device/condition/replicate/branch.
   Keep every source row and window decision; report signed decades/V slope,
   residuals and labeled signed/magnitude reciprocal. No best-window search,
   sign repair, point trimming or automatic subthreshold-regime certification.

8. **[Ordered I–V Signed-Path Integral](tools/iv-loop-integral/README.md)**
   Compute a signed trapezoidal I dV descriptor for each exact declared
   device/condition/cycle/replicate loop, with explicit outgoing-edge branch
   labels and complete observation/edge lineage. Require exact voltage closure;
   disclose unequal current endpoints and signed cancellation. No added closure,
   unsigned lobe area, physical-energy calculation or intrinsic-hysteresis claim.

9. **[BOSCH Identity and Channel-Name Audit](tools/bosch-identity-audit/README.md)**
   Inventory nullable identities from the BOSCH v1 nine-point CSV while keeping
   other value fields opaque. Align explicit 31/44-column metadata by exact name,
   preserve duplicates/missing labels and report only candidate name joins.
   No dataset download, NetCDF decoder, target eligibility or automatic exclusions.

10. **[I–V Voltage-Grid Audit](tools/iv-grid-audit/README.md)**
    Compare exact voltage-coordinate sets across explicitly labeled I–V sweeps.
    Preserve full device/condition/sweep/branch provenance, duplicate observations
    and missing-from-observed-union coordinates. Extra response/target fields stay
    opaque. No planned-grid inference, row repair, fitting or independent-DUT claim.

## Quick start

For tool selection, adapting laboratory CSV exports, and checked end-to-end
commands, see the [practical measurement workflow guide](docs/workflows.md).

Python **3.10+**. No package installation, API key, external service, downloaded
model, or internet access is needed. Run from the repository root:

```sh
python3 tools/iv-readpoint/iv_readpoint.py \
  tools/iv-readpoint/examples/synthetic_iv.csv \
  --voltage-unit V --current-unit uA \
  --read-voltage 100 --read-voltage-unit mV --stdout

python3 tools/rram-window/rram_window.py \
  tools/rram-window/fixtures/synthetic.csv \
  --voltage-unit V --resistance-unit ohm \
  --hrs-label HRS --lrs-label LRS --stdout

python3 tools/tlm-extract/tlm_extract.py \
  tools/tlm-extract/examples/synthetic_tlm.csv \
  --length-unit um --resistance-unit ohm \
  --accept-linear-tlm-assumptions --stdout

python3 tools/vth-constant-current/vth_constant_current.py \
  tools/vth-constant-current/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit uA \
  --criterion-current 1 --criterion-current-unit uA \
  --current-polarity positive --accept-transfer-assumptions --stdout

python3 tools/hall-response/hall_response.py \
  tools/hall-response/examples/synthetic_hall.csv \
  --field-unit T --voltage-unit mV --current-unit mA \
  --accept-hall-assumptions --stdout

python3 tools/sweep-segments/sweep_segments.py \
  tools/sweep-segments/examples/synthetic_hall_coordinates.csv \
  --axis magnetic-flux-density --coordinate-unit mT --stdout

python3 tools/transfer-log-slope/transfer_log_slope.py \
  tools/transfer-log-slope/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit nA --current-polarity positive \
  --window-min 0.1 --window-max 0.4 --accept-log-transfer-assumptions --stdout

python3 tools/iv-loop-integral/iv_loop_integral.py \
  tools/iv-loop-integral/examples/01_triangle.csv \
  --voltage-unit V --current-unit uA \
  --accept-supplied-path-assumptions --stdout

python3 tools/bosch-identity-audit/bosch_identity_audit.py \
  tools/bosch-identity-audit/examples/synthetic_inventory.csv \
  --channel-metadata tools/bosch-identity-audit/examples/synthetic_channels.json \
  --stdout

python3 tools/iv-grid-audit/iv_grid_audit.py \
  tools/iv-grid-audit/examples/synthetic_equal.csv \
  --voltage-unit V --stdout

python3 run_tests.py
```

The I–V, TLM, threshold-criterion, Hall-response, sweep-audit, transfer-log-slope,
I–V signed-path, BOSCH metadata and equal-voltage-grid quick-start fixtures exit `0`. The BOSCH
audit retains incomplete/duplicate records and missing labels even on exit `0`;
it does not select an eligible scientific cohort.
The RRAM fixture deliberately includes missing/invalid pairs and exits `1` while producing its diagnostic JSON report; this is expected,
not a crash. The test runner exits `0` when its software tests pass.

Use `--help` on each CLI for the exact options. `--stdout` explicitly opts in to
printing raw device identifiers, measurements, and derived features. To keep a
report in a file, replace `--stdout` with `--output NEW_PATH.json`; the path must
not already exist. Reports are created exclusively with requested POSIX mode
`0600`; no existing file or symlink is overwritten. Filesystem permissions on
other platforms may differ.

Consult each tool's README for exact exit-status semantics: `0` and `1` reflect
that tool's documented checks and exclusions, not a shared scientific-quality
rating. All use `2` for a fatal input/CLI/I/O error. A report can contain useful
features and exclusions together: inspect its counts and warnings, not just
whether JSON was produced. The sweep and BOSCH metadata audits use only `0` (complete audit, not
scientific validation) and `2`; neither selects a cohort or uses status `1`.
The voltage-grid audit uses `1` for duplicate coordinates or unequal observed
grids, with all observations retained and response values uninspected.
A write failure may leave an incomplete new file; discard it after a
fatal exit. None of the programs sends data anywhere or changes the input file.

## End-to-end RRAM feature example

The [measurement-to-features example](examples/measurement-to-features/README.md)
connects two existing tools: declared HRS/LRS I–V branches → signed readpoints →
explicit positive static `V/I` resistance → paired RRAM windows → **one row per
physical-device ID**. Repeated cycles stay within devices; batch and condition
metadata, source-row lineage and a predictor allowlist accompany the feature CSV.
Missing/duplicate joins and condition changes fail rather than being filled in.

```sh
python3 examples/measurement-to-features/build_features.py \
  --input-dir examples/measurement-to-features/fixtures \
  --output-dir /tmp/invented-rram-features \
  --max-bracket-span-V 0.11 --accept-measurement-assumptions
```

Choose a new output directory. The original deterministic generator and its
invented six-device fixtures are included; the example accepts SI inputs only.
Read its assumptions before adapting laboratory data. It is a two-tool workflow,
not an additional measurement tool, a trained ML model, or real-device validation.

## End-to-end FET branch feature example

The [FET measurement-to-features workflow](examples/fet-measurement-to-features/README.md)
runs the current-criterion and explicit-window log-slope extractors on the same
transfer-CSV bytes. It retains both complete raw-data audits, all device/branch
IDs and exact row lineage, and exports a per-branch table plus a two-column
numeric predictor CSV and calculator-compatible feature-name allowlist.

```sh
python3 examples/fet-measurement-to-features/build_features.py \
  examples/fet-measurement-to-features/fixtures/transfer.csv \
  --voltage-unit V --current-unit nA \
  --criterion-current 50 --criterion-current-unit nA \
  --current-polarity positive --max-bracket-span 0.1 \
  --window-min 0.1 --window-max 0.4 \
  --accept-measurement-assumptions --output-dir /tmp/invented-fet-features
```

Use a new output directory. Sixteen invented branches represent four namespaced
device labels, not sixteen independent devices. If either extractor rejects any
branch, only audits are saved (exit `1`), with no partial-cohort feature export.
The log slope is descriptive, not a certified subthreshold swing. The allowlist
excludes IDs/metadata but cannot establish suitability for an unspecified target:
never use a target or its algebraic proxy as a feature. No target or model is made.

## A semiconductor feature workflow

1. **Preserve the measurement context.** Keep device/wafer/lot provenance,
   acquisition order, units, polarity convention, temperature, pulse/read timing,
   compliance, and state history with the original measurement record.
2. **Declare branches rather than infer them.** Export each fixed-state,
   fixed-protocol monotonic segment with a distinct branch label. The I–V tool
   turns a chosen read voltage into one signed current/static-conductance feature
   per device, cycle, and branch, with the exact source rows used. The optional
   [sweep audit](tools/sweep-segments/README.md) maps coordinate-direction changes
   and plateaus without deleting samples, but does not establish chronology or
   fixed conditions and is not an automatic branch exporter.
3. **Pair only intended resistance states.** For RRAM measurements, explicitly
   label HRS/LRS and provide a read-condition ID naming the relevant protocol.
   Run the window evaluator only on intended pairs. It compares declarations;
   it cannot establish that temperature, timing, compliance, or actual device
   state really matched in the laboratory.
4. **Review exclusions and physical assumptions.** Do not convert absent or
   rejected results to zero. Check instrument limits and read disturb yourself.
   An interpolated readpoint does not prove a nondisturbing read condition.
5. **Build downstream models without device leakage.** Retain provenance and
   separate training/evaluation by the experiment's independent physical unit,
   such as device, wafer, or lot. Repeated cycles are not extra independent
   devices. Splitting, model fitting, uncertainty analysis, and evaluation remain
   the responsibility of the downstream workflow.

The tools are deliberately separate. The I–V extractor does **not** automatically
assign HRS/LRS labels or turn its signed `I/V` values into a resistance-window
input. Any conversion and state assignment require an explicit, justified
measurement protocol; no hidden automatic handoff makes those decisions.

TLM input is separate as well: it requires a defensible total resistance for each
contact pair and known geometry, not automatically converted I–V features. Its
linear regression is a declared measurement model, not a trained ML model. A
positive coefficient or high R-squared does not validate equal contacts, Ohmic
behavior, removed parasitics, or useful precision in the extrapolated intercept.

The threshold-criterion extractor also requires its own transfer-sweep input,
including fixed temperature, source-referenced biases and a signed current
criterion. Its operational VGS value depends on that declared criterion and
protocol. It does not infer a universal physical Vth, identify transistor type,
choose a default current, or remove differences between sweep branches.

Hall response uses its own signed field/current/voltage input and documented
wiring/field-axis protocol. Its least-squares slope and intercept are descriptive,
not a validated physical Hall model. A zero-current group is a voltage-background
diagnostic only; no carrier density, type, mobility or thickness is inferred.

Transfer log-slope uses an explicit voltage window, all measured samples inside
it and a declared current polarity. Its signed reciprocal can support a
separately justified subthreshold analysis, but neither a good fit nor a small
reciprocal certifies the regime. Window choice, noise floor and physical
interpretation remain the user's responsibility; every row stays auditable.

## Guardrails and limitations

- Only explicitly declared units are converted. Unit or state inference is absent.
- Measurement extractors use strict CSV schemas that reject extra columns. The
  voltage-grid audit explicitly leaves extra values opaque, and the BOSCH adapter
  decodes only its declared identity fields. See each tool's selected-column,
  blank-line and numeric-validation contract.
- Interpolation is local to adjacent measured samples, not a physical device model. Static
  `I/V` is not differential `dI/dV`, conductivity, or proof of Ohmic conduction.
- Constant-current extraction uses linear signed current versus VGS, not
  log-current interpolation. Bracket span is not measurement uncertainty.
- HRS/LRS are user labels, not conclusions. A ratio below one is retained and
  flagged rather than relabeled, inverted, or treated as successful switching.
- The software does not infer SET/RESET thresholds, microscopic mechanisms,
  endurance, retention, reliability, or lifetime. It does not validate equipment,
  calibration, a device technology, or a scientific result.
- There are no private papers, figures, review records, device datasets, or
  external API integrations in the distribution. Examples are synthetic.

## Verification

The original eight tools have been executed with **Python 3.12.14 and 3.13.5
on Linux**. The BOSCH metadata and voltage-grid audit tools have been executed on
**Python 3.12.14**.
The source targets Python 3.10+, but Python 3.10/3.11 and other versions or
operating systems remain unverified. **CI is not configured**: this distribution includes no active
GitHub Actions workflow and starts no hosted or paid compute jobs.

Run `python3 run_tests.py` to execute all ten isolated tool suites plus the
end-to-end example suites. They cover
unit normalization, numerical boundaries, strict schemas, device/branch/state
isolation, TLM equations and grouped fits, signed current-criterion crossings,
fixed bias/temperature declarations, signed Hall fits and residuals, user-windowed
base-10 transfer fits and selection audits, exact
coordinate edge partitions and shared sample identities, exact voltage-grid
coverage and opaque-response noninterference, exact signed I–V path
integrals with explicit branch ownership and voltage-closure/numeric-range gates, missing and
invalid data, strict feature-table
joins, repeated-cycle aggregation, deliberate output, and no-overwrite safety.
Passing these tests establishes software behavior on the tested runtime, not
hardware accuracy, scientific validity, or real-device performance. Source-level
Python 3.10+ compatibility does not mean all those versions were executed.

## Roadmap

These are proposed directions, **not implemented capabilities**:

- Additional MOSFET transfer-curve features beyond the implemented constant-
  current criterion and user-windowed log slope, with explicit bias conditions
  and documented definitions.
- Grouped feature-table workflows beyond the strict complete-pair RRAM and
  complete-branch FET examples,
  with explicit handling of missingness, censoring and other sampling designs.
- RRAM cycle/retention diagnostics with explicit censoring and physical stopping
  criteria, without extrapolated lifetime claims.
- Measurement-uncertainty propagation and instrument compliance/noise-floor
  annotations for readpoint features.
- Reproducible device-level ML baselines with leakage checks, calibration, and
  documented applicability limits, once suitable openly licensed data exist.

New tools should answer a concrete semiconductor measurement or modeling need,
state their physical assumptions, use synthetic or openly licensed fixtures,
include meaningful tests, and avoid generic utilities disguised by a domain
name. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Original software and synthetic fixtures. The BOSCH adapter
references a separately licensed CC BY 4.0 dataset without redistributing its
source payloads; see its attribution section. Background references
linked in the tool documentation explain measurement context; their data and
figures are not redistributed here.
