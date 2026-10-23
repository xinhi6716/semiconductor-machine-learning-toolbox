# Semiconductor Machine Learning Toolbox

Small, inspectable tools for **semiconductor measurement preprocessing and
ML-ready feature extraction**. This repository is semiconductor-only: generic
CSV utilities, experiment runners, and unrelated ML demos do not belong here.

The first usable core covers semiconductor I–V measurements, resistive RAM
(RRAM/ReRAM), and conventional linear transfer-length-method (TLM) contact
measurements. All three command-line tools use only the Python standard library,
require explicit units, preserve measurement provenance, and expose exclusions
instead of silently repairing questionable measurements.

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

## Quick start

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

python3 run_tests.py
```

The I–V and TLM fixtures exit `0`. The RRAM fixture deliberately includes missing/invalid
pairs and exits `1` while producing its diagnostic JSON report; this is expected,
not a crash. The test runner exits `0` when its software tests pass.

Use `--help` on each CLI for the exact options. `--stdout` explicitly opts in to
printing raw device identifiers, measurements, and derived features. To keep a
report in a file, replace `--stdout` with `--output NEW_PATH.json`; the path must
not already exist. Reports are created exclusively with requested POSIX mode
`0600`; no existing file or symlink is overwritten. Filesystem permissions on
other platforms may differ.

CLI exit statuses are `0` for a fully usable report, `1` for a report containing
excluded groups, and `2` for a fatal input/CLI/I/O error. A report can contain useful
features and exclusions together: inspect its counts, not just whether JSON was
produced. A write failure may leave an incomplete new file; discard it after a
fatal exit. None of the programs sends data anywhere or changes the input file.

## A semiconductor feature workflow

1. **Preserve the measurement context.** Keep device/wafer/lot provenance,
   acquisition order, units, polarity convention, temperature, pulse/read timing,
   compliance, and state history with the original measurement record.
2. **Declare branches rather than infer them.** Export each fixed-state,
   fixed-protocol monotonic segment with a distinct branch label. The I–V tool
   turns a chosen read voltage into one signed current/static-conductance feature
   per device, cycle, and branch, with the exact source rows used.
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

## Guardrails and limitations

- Only explicitly declared units are converted. Unit or state inference is absent.
- Strict CSV schemas reject extra columns rather than silently discard conditions.
  See each tool's documented blank-line and numeric-validation behavior.
- Linear interpolation is local in voltage, not a physical device model. Static
  `I/V` is not differential `dI/dV`, conductivity, or proof of Ohmic conduction.
- HRS/LRS are user labels, not conclusions. A ratio below one is retained and
  flagged rather than relabeled, inverted, or treated as successful switching.
- The software does not infer SET/RESET thresholds, microscopic mechanisms,
  endurance, retention, reliability, or lifetime. It does not validate equipment,
  calibration, a device technology, or a scientific result.
- There are no private papers, figures, review records, device datasets, or
  external API integrations in the distribution. Examples are synthetic.

## Verification

Only **Python 3.12.14 on Linux** has been executed and verified locally. The
source targets Python 3.10+, but other versions and operating systems are
unverified. **CI is not configured**: this distribution includes no active
GitHub Actions workflow and starts no hosted or paid compute jobs.

Run `python3 run_tests.py` to execute all three isolated `unittest` suites. They cover
unit normalization, numerical boundaries, strict schemas, device/branch/state
isolation, TLM equations and grouped fits, missing and invalid data, deliberate
output, and no-overwrite safety.
Passing these tests establishes software behavior on the tested runtime, not
hardware accuracy, scientific validity, or real-device performance.

## Roadmap

These are proposed directions, **not implemented capabilities**:

- MOSFET transfer-curve features with explicit bias conditions and documented
  threshold-voltage extraction definitions.
- Semiconductor-specific grouped feature tables that preserve wafer/device/lot
  provenance and distinguish repeated sweeps from independent devices.
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

[MIT](LICENSE). Original software and synthetic fixtures. Background references
linked in the tool documentation explain measurement context; their data and
figures are not redistributed here.
