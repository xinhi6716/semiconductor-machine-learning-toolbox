# Choose a measurement workflow and check its output

This guide is for preparing semiconductor measurements, running the tools, and
deciding what may safely enter a feature table. Start with the question your
measurement can answer, not the file extension your instrument exports.

All commands below run offline from the repository root with Python 3.10+ and
the standard library. The examples are invented arithmetic fixtures, not
experimental data or device simulations. Their expected results check software
behavior, not measurement accuracy, physical mechanisms, or device performance.

## 1. Choose the tool before adapting the CSV

| What you already have | Use | What the result means |
| --- | --- | --- |
| Signed current versus device voltage, with known acquisition branches | [I–V readpoint](../tools/iv-readpoint/README.md) | Signed current and static `I/V` at your chosen nonzero read voltage, separately per device/cycle/branch. |
| Explicitly labeled HRS and LRS resistance observations | [RRAM window](../tools/rram-window/README.md) | Dimensionless `R_HRS / R_LRS` for matching device/cycle/read-condition/bias pairs. |
| Total contact-pair resistances at known gaps and width in a conventional rectangular TLM structure | [Linear TLM](../tools/tlm-extract/README.md) | Sheet resistance and per-contact resistance under the declared linear-TLM assumptions. |
| Transfer sweeps with known source-referenced biases and temperature | [Current-criterion Vth](../tools/vth-constant-current/README.md) | Signed VGS where signed ID reaches your declared current criterion. |
| Signed transverse voltage versus magnetic flux density with fixed current, temperature and known wiring/field axis | [Hall response](../tools/hall-response/README.md) | Descriptive signed slope/intercept and residuals; current-normalized apparent sensitivity only at nonzero current. |
| Transfer sweeps with a scientifically justified, explicitly chosen voltage window | [Transfer log-slope](../tools/transfer-log-slope/README.md) | Descriptive log10-current slope and labeled reciprocal, not a certified subthreshold swing. |
| Ordered voltage or magnetic-flux-density samples whose direction changes need inspection | [Sweep segment audit](../tools/sweep-segments/README.md) | Exact contiguous edge runs and shared-endpoint identities; no inferred chronology or automatically valid physical branches. |
| Declared HRS/LRS I–V branches plus complete device and condition records | [RRAM feature example](../examples/measurement-to-features/README.md) | A strict two-tool workflow ending in one descriptive feature row per device. |

These quantities are not interchangeable. Static `I/V` is not differential
`dI/dV` or material conductivity. Static `V/I` is not a fitted TLM resistance or
an isolated contact property. A resistance ratio does not identify a switching
mechanism. Criterion Vth is an operational definition, not a universal physical
threshold. Hall response is not a carrier density or mobility measurement. The
feature example does not combine TLM, Vth, Hall or transfer-log-slope data with RRAM data,
and none of these workflows trains or validates an ML model.

## 2. Adapt an instrument export without losing its meaning

Keep the original export unchanged. Make a separate, documented adapter output
with exactly the selected tool's columns. Extra columns are rejected, so retain
settings and raw-to-adapted row mappings in a separate measurement record rather
than silently throwing them away. Record your adapter version, unit conversion,
selected acquisition segments, command arguments, and known limitations.

Before running, resolve these questions from actual acquisition/sample records:

1. **Which physical sample and acquisition is this?** Preserve device identity
   across repeated cycles. Namespace local device names with known wafer/lot
   identity where needed. Do not rename repeated acquisitions as new devices.
2. **What are the units and terminals?** Column names such as `Voltage` and `I1`
   are not sufficient evidence. Confirm units and terminal/sign conventions from
   the export configuration. Convert mixed-unit inputs explicitly before use;
   one CLI unit declaration applies to the whole column, not individual rows.
3. **Where does each branch start and stop?** Use the acquisition log to split
   distinct monotonic, fixed-protocol segments. Preserve their original order.
   Sorting a triangular sweep by voltage would erase its history, not repair it.
   The [ordered sweep audit](../tools/sweep-segments/README.md) can map candidate
   breaks and flat intervals without dropping samples. Its supplied-order
   mapping does not prove chronology or stable conditions; inspect the original
   records before declaring downstream branches, and retain shared-endpoint
   identities rather than counting repeated references as new measurements.
4. **What does a condition ID stand for?** Keep a traceable definition covering
   relevant bias, temperature, timing, state preparation, compliance and instrument
   settings. Identical strings only establish identical declarations. They do not
   prove that conditions were stable or matched in the laboratory.
5. **Which state or criterion was intended?** HRS/LRS labels must come from the
   documented protocol, not resistance ranking. Choose a read voltage or signed
   current criterion for the measurement question, not to make an extraction pass.

For example, rename an instrument's `Gate[V]` to `gate_voltage` only after checking
that it is VGS. If it is an absolute gate output, obtaining VGS requires the actual
source reference, not a guessed zero. Do not populate missing temperature with
`300`, missing body bias with `0`, or unknown HRS/LRS labels from the observed
current. If those declarations cannot be recovered, stop that dependent workflow.
An honest unknown-provenance label may document a limitation where permitted by
the tool, but cannot establish fixed conditions or distinguish acquisitions that
the records cannot distinguish.

Use these exact schemas. Column order may differ; names are case-sensitive.

- I–V: `device,cycle,branch,voltage,current`. A group is the first three fields.
  Voltage units are `V`, `mV`, `uV`; current units are `A`, `mA`, `uA`, `nA`, `pA`.
  There is no condition column: partition and label actual fixed-condition
  acquisitions without merging them, and retain their condition mapping separately.
- RRAM: `device,cycle,state,read_condition,read_voltage,resistance`. There must
  be at most one observation per device/cycle/state. Required state labels are
  supplied explicitly. Resistance units are `ohm`, `kohm`, `Mohm`; voltage units
  are `V`, `mV`, `uV`. Pairing is by device/cycle, not merely by adjacent rows.
- TLM: `lot,wafer,structure,condition,replicate,pair,spacing,width,resistance`.
  The first five fields define one fit; `pair` names one contact-pair observation.
  Spacing means edge-to-edge gap, not center-to-center pitch. Both lengths share
  `m`, `mm`, `um`, or `nm`; resistance uses `ohm`, `kohm`, or `Mohm`. A group needs
  at least three distinct positive gaps and one exactly equal positive width.
- Vth criterion:
  `lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current`.
  The first six fields define one branch. Temperature is kelvin; the three
  voltages are VDS, VBS, VGS in the declared voltage unit. VDS must be nonzero,
  temperature positive, and temperature/VDS/VBS exactly constant within a group.
  Current uses the declared current unit, not current density. A group needs at
  least two samples and one signed current convention.
- Hall response:
  `lot,wafer,device,condition,replicate,branch,temperature_K,bias_current,magnetic_field,transverse_voltage`.
  The first six fields define a strictly monotonic field branch with at least
  three distinct fields, fixed signed current and positive fixed kelvin temperature.
  B units are `T`, `mT`, `uT`, `G`, `kG`, not H in A/m; voltage units are `V`, `mV`,
  `uV`, `nV`; current units are `A`, `mA`, `uA`, `nA`, `pA`. Document unchanged
  ordered voltage terminals, current direction and perpendicular field axis in
  the condition protocol. Zero current permits a background diagnostic only.

Use plain UTF-8 comma-separated CSV, decimal/scientific numbers without unit
suffixes, and exact unpadded identifiers. Treat IDs such as `001` as strings, not
spreadsheet numbers. A blank/invalid numeric value must not be filled with zero.
The tools differ in blank-row and numeric-error handling; consult their linked
CSV contracts rather than assuming one parser policy applies to every tool.
The optional sweep audit uses its own coordinate-only schema and strict
contiguous sequence contract; see its [input contract](../tools/sweep-segments/README.md#input-contract).

The [transfer-log-slope tool](../tools/transfer-log-slope/README.md) uses the same
11-column transfer schema as current-criterion Vth, but requires at least three
selected samples and a user-chosen voltage window. Do not automatically reuse
a threshold bracket as a physically justified subthreshold window.

## 3. Run and inspect the five supplied fixtures

Run the blocks in order in the same POSIX shell. First create a fresh private
parent directory; each tool will exclusively create a new report inside it.
Rerunning this setup gives new destinations without overwriting earlier reports.

```sh
RUN_DIR="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="semiconductor-workflow-"))')"
export RUN_DIR
printf 'Reports: %s\n' "$RUN_DIR"
```

The interpolation limits below are chosen to exercise these fixtures. They are
not recommended limits for a real experiment or estimates of uncertainty.

### A. Read a signed I–V point without mixing branches

```sh
python3 tools/iv-readpoint/iv_readpoint.py \
  tools/iv-readpoint/examples/synthetic_iv.csv \
  --voltage-unit V --current-unit uA \
  --read-voltage 100 --read-voltage-unit mV \
  --max-bracket-span 110 --output "$RUN_DIR/iv.json"
```

Expected exit: `0`. Nine observations give four readpoints at `0.1 V`.
For `synthetic_A`, cycle `1`, the up branch gives about `10 uA` and `100 uS`;
the down branch gives `8 uA` and `80 uS`. They remain separate. `synthetic_B`
has an exact measured readpoint; the other three use adjacent interpolation.
Check `method`, `source_rows`, `acquisition_direction` and `bracket_span_V`.

Here `110` means **110 mV**, because the I–V bracket option uses
`--read-voltage-unit`, not the input-voltage unit. Exact hits do not need a
bracket. Zero current is allowed; zero read voltage is not. Duplicate voltages,
reversals and out-of-range targets are rejected, not sorted, averaged or
extrapolated. A branch label does not automatically assign an HRS/LRS state.

### B. Pair declared RRAM resistances and retain exclusions

```sh
if python3 tools/rram-window/rram_window.py \
  tools/rram-window/fixtures/synthetic.csv \
  --voltage-unit V --resistance-unit ohm \
  --hrs-label HRS --lrs-label LRS --output "$RUN_DIR/rram.json"
then
  rram_status=0
else
  rram_status=$?
fi
printf 'RRAM fixture exit: %s (expected 1)\n' "$rram_status"
test "$rram_status" -eq 1
```

Expected tool exit: `1`, with a valid diagnostic report, not a crash. There are
13 rows, seven device/cycle groups, four usable pairs, and three excluded groups:
missing LRS, a read-voltage mismatch, and invalid resistance. Usable ratios are
`100`, `90`, `20`, and `0.8`; the last stays below one and is flagged.

Both states need matching condition IDs and exactly equal signed nonzero read
voltages. `0.1` and `0.10` match, but `0.1` and `-0.1` do not. The fixture's
`device_B` has valid pairs at different polarities, so its overall
`resistance_ratio_summary` is null and separate strata are retained. Do not
average those strata just because the device ID matches. Select pairs by
`status == "valid"`, not by the presence of a voltage field.

The explicit shell check above applies only to this deliberately imperfect
fixture. Do not normalize every future exit `1` into success without reviewing
which measurements were excluded and whether that changes your analysis.

### C. Fit conventional linear TLM, not an arbitrary resistance trend

```sh
python3 tools/tlm-extract/tlm_extract.py \
  tools/tlm-extract/examples/synthetic_tlm.csv \
  --length-unit um --resistance-unit ohm \
  --accept-linear-tlm-assumptions --output "$RUN_DIR/tlm.json"
```

Expected exit: `0`. Both replicate fits give `100 ohm/square`; per-contact
resistances are `5 ohm` and `6 ohm`. Their intercepts are `10 ohm` and `12 ohm`,
because the model is `R_total = R_sheet * gap / width + 2 * R_contact`.
Read `observations` and residuals alongside `fit` and `physical_estimates`.

The flag declares equal Ohmic contacts, uniform channel/width, fixed conditions
and negligible or already removed parasitics. The program cannot verify them.
Even these perfect synthetic fits warn that the zero-gap intercept is extrapolated
and that rows and replicate groups are not independent devices. Repeated gaps
with distinct pair IDs receive equal **row** weight, not equal gap weight.
A negative intercept or nonpositive slope retains signed fit diagnostics but
rejects the physical estimates; it is never clipped into a positive result.
No transfer length or specific contact resistivity is calculated.

### D. Extract an operational signed current-criterion Vth

```sh
python3 tools/vth-constant-current/vth_constant_current.py \
  tools/vth-constant-current/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit uA \
  --criterion-current 1 --criterion-current-unit uA \
  --current-polarity positive --max-bracket-span 0.41 \
  --accept-transfer-assumptions --output "$RUN_DIR/vth.json"
```

Expected exit: `0`. At signed ID = `1 uA`, the forward branch gives VGS = `0.6 V`
and the reverse branch `0.7 V`. Their `0.4 V` interpolation brackets differ and
remain in acquisition order. The output does not combine the branches or assign
a physical cause to their difference. Inspect `extraction`, `conditions`,
`observations`, and `warnings` together.

This bracket limit uses the **input voltage unit**, unlike the I–V option.
Interpolation is linear in signed current, not log current. There is no default
criterion or automatic W/L normalization. If a protocol uses a geometry-scaled
criterion, calculate and document the actual current separately; devices needing
different criteria require separate runs. `positive`/`negative` describes current
sign, not transistor type. Negative data require a negative criterion, for example
the option spelling `--criterion-current=-1`, with the appropriate declared unit.

The entire branch must meet the monotonicity checks. A unique crossing on an
otherwise nonmonotonic current trace is rejected; multiple crossings, exact-hit
plateaus and absent crossings are also rejected. Do not delete noisy points or
take absolute currents merely to obtain a value from this conservative extractor.

### E. Describe a Hall branch without inferring carrier properties

```sh
python3 tools/hall-response/hall_response.py \
  tools/hall-response/examples/synthetic_hall.csv \
  --field-unit T --voltage-unit mV --current-unit mA \
  --accept-hall-assumptions --output "$RUN_DIR/hall.json"
```

Expected exit: `0`. Twenty invented points produce four separate fits for two
physical-device labels. Invented A's increasing and decreasing branches retain
intercepts of `0.0003 V` and `0.0004 V`; both have slope `0.002 V/T` at `0.001 A`.
Its negative-current replicate has negative slope, but the same signed slope/current
ratio, `2 V/(A T)`. Invented B is deliberately curved and warns about nonzero
residuals while still providing a descriptive fit. Warnings are not rejections.

Inspect every residual, fixed condition and branch separately. A fitted intercept
is not an isolated offset and is not subtracted. Both field polarities do not imply
matched reversal pairs; this tool does no antisymmetrization or offset cancellation.
Nonzero residuals may reflect noise, curvature, rounding or drift, while zero
residuals cannot validate Hall physics. No carrier density, mobility, thickness or
carrier sign is inferred. See the tool's README for the full scope boundary.

### Optional: inspect a declared transfer log-current window

This separate example adds no automatic handoff to the five-tool checks below:

```sh
python3 tools/transfer-log-slope/transfer_log_slope.py \
  tools/transfer-log-slope/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit nA --current-polarity positive \
  --window-min 0.1 --window-max 0.4 \
  --accept-log-transfer-assumptions --output "$RUN_DIR/transfer-log.json"
```

Expected exit: `0`. Four branches remain separate; 16 of 24 invented points are
fitted and all eight outside-window rows remain auditable. Invented A's two
branches both give `10 decades/V` and a signed reciprocal `100 mV/decade`;
invented B retains curvature residuals; invented C keeps `-10 decades/V` and
signed reciprocal `-100 mV/decade`. The separate reciprocal magnitude does not
replace that sign or certify a subthreshold swing.

Both window bounds use the input voltage unit, are inclusive, and select only
measured points. Selected zero/wrong-sign currents reject the group rather than
being removed; nonmonotonic current stays in the fit with a warning. Review
`observations`, `selected_source_rows`, `outside_window_source_rows`, residuals,
actual selected ranges and `warnings`. The original numeric cell text and input
hash accompany the report. There is no search for an attractive window and no
automatic determination of the subthreshold regime, noise floor or compliance.

### Check the arithmetic and inspect actual report fields

This check is specific to the supplied fixtures. It prints counts and verifies
representative values with floating-point tolerance, not metrological tolerance.

```sh
python3 - <<'PY'
import json
import math
import os
from pathlib import Path

root = Path(os.environ["RUN_DIR"])
reports = {name: json.loads((root / (name + ".json")).read_text())
           for name in ("iv", "rram", "tlm", "vth", "hall")}
for name, report in reports.items():
    print(name, report["counts"])
iv = reports["iv"]
assert iv["counts"]["extracted_groups"] == 4
point = next(r for r in iv["results"] if
             (r["device"], r["cycle"], r["branch"]) ==
             ("synthetic_A", "1", "post_program_up"))
assert math.isclose(point["current_A"], 1e-5)
assert math.isclose(point["conductance_S"], 1e-4)
rram = reports["rram"]
assert rram["counts"]["valid_pairs"] == 4
assert rram["counts"]["excluded_groups"] == 3
assert sorted(p["resistance_ratio"] for p in rram["pairs"]
              if p["status"] == "valid") == [0.8, 20.0, 90.0, 100.0]
for result in reports["tlm"]["results"]:
    estimate = result["physical_estimates"]
    assert estimate["sheet_resistance_ohm_per_square"] == 100.0
    assert estimate["per_contact_resistance_ohm"] == {"r1": 5.0, "r2": 6.0}[result["replicate"]]
for result in reports["vth"]["results"]:
    value = result["extraction"]["gate_source_voltage_at_criterion_V"]
    assert math.isclose(value, {"forward": 0.6, "reverse": 0.7}[result["branch"]])
hall = reports["hall"]
assert hall["counts"]["input_groups"] == 4
assert hall["counts"]["distinct_physical_device_ids"] == 2
assert [r["current_normalized_sensitivity_V_per_A_T"] for r in hall["results"]] == [2, 2, 2, 1]
assert math.isclose(hall["results"][-1]["fit"]["max_abs_residual_V"], 0.0001)
assert "nonzero_residuals_possible_nonlinearity_noise_or_drift" in hall["results"][-1]["warnings"]
print("Five fixture report checks passed.")
PY
```

## 4. Build the complete-pair RRAM device feature table

Use the [example's input contract and assumption checklist](../examples/measurement-to-features/README.md)
before adapting laboratory data. It requires three synchronized files:

- `iv_sweeps.csv`: the I–V schema above, already converted to V and A.
- `manifest.csv`: `batch,wafer,device,cycle,replicate,branch,state,condition`.
  Exactly one row maps each measured branch to its declared state and provenance;
  each device/cycle has one HRS and one LRS branch. Paired states share a replicate.
- `conditions.csv`: `condition,read_voltage_V,temperature_K,read_delay_ms,protocol`.
  Every used condition has one definition, with no unknown or unused definitions.

The device ID is globally unique and remains in one batch, wafer and condition
throughout this bundle. Cycles are repeated measurements, not devices. The
cycle/replicate relationship is one-to-one within each device; a new replicate
label cannot repair duplicate upstream device/cycle/branch keys. A real change
of condition requires a separately justified workflow, not an invented device ID
or a hidden replacement with a single condition label.

Generate fresh inputs and compare them byte-for-byte with the checked-in fixtures:

```sh
python3 examples/measurement-to-features/generate_synthetic.py \
  --output-dir "$RUN_DIR/generated-input"

python3 - <<'PY'
import os
from pathlib import Path
generated = Path(os.environ["RUN_DIR"]) / "generated-input"
fixtures = Path("examples/measurement-to-features/fixtures")
for name in ("iv_sweeps.csv", "manifest.csv", "conditions.csv"):
    assert (generated / name).read_bytes() == (fixtures / name).read_bytes(), name
print("Generated inputs match the checked-in fixtures.")
PY

python3 examples/measurement-to-features/build_features.py \
  --input-dir "$RUN_DIR/generated-input" \
  --output-dir "$RUN_DIR/device-features" \
  --max-bracket-span-V 0.11 --accept-measurement-assumptions
```

Both CLIs should exit `0`. The generator invents two batches, six devices,
18 cycles, 36 state branches and 108 I–V points. The pipeline explicitly converts
readpoints to static `V/I` resistance, requiring nonzero current with the same
sign as read voltage, then invokes the RRAM evaluator. It does not use absolute
values to rescue a sign mismatch. The required flag declares the documented
terminal, state-preserving read, interpolation and conversion assumptions; it
does not experimentally verify them.

```sh
python3 - <<'PY'
import csv
import json
import math
import os
from pathlib import Path

root = Path(os.environ["RUN_DIR"]) / "device-features"
with (root / "features.csv").open(newline="") as stream:
    rows = list(csv.DictReader(stream))
schema = json.loads((root / "feature_schema.json").read_text())
windows = json.loads((root / "rram_windows.json").read_text())
assert len(rows) == len({r["device"] for r in rows}) == 6
assert all(r["cycle_count"] == "3" for r in rows)
assert len({r["split_group"] for r in rows}) == 2
assert windows["counts"]["valid_pairs"] == 18
assert schema["target_columns"] == []
assert schema["model_feature_allowlist"] == [
    "hrs_resistance_median_ohm", "lrs_resistance_median_ohm", "resistance_ratio_median"]
first = next(r for r in rows if r["device"] == "synthetic_device_1_1")
assert math.isclose(float(first["hrs_resistance_median_ohm"]), 1e6 / 3)
assert math.isclose(float(first["lrs_resistance_median_ohm"]), 1e6 / 101)
assert math.isclose(float(first["resistance_ratio_median"]), 101 / 3)
print("Six device rows, three cycles each, 18 valid pairs; feature checks passed.")
PY
```

Read the six output files as a bundle, not just `features.csv`:

- `features.csv` contains device-level medians and provenance columns. The ratio
  median is the median of within-cycle paired ratios, not generally the ratio of
  separately aggregated HRS and LRS medians.
- `feature_schema.json` identifies the three candidate predictors, their units,
  metadata columns, and the deliberately empty target list.
- `iv_readpoints.json` records branch readpoints and brackets in an
  example-specific wrapper, not the standalone I–V CLI's top-level schema.
- `resistance_input.csv` is the exact derived input to the window evaluator;
  `rram_windows.json` retains its full pair report.
- `provenance.json` links inputs, code fingerprints, conditions, device repeats,
  manifest rows and original I–V source lines. Hashes cannot replace the originals.

This example is deliberately all-or-nothing. A missing/duplicate join, rejected
readpoint, invalid conversion, or condition change fails with exit `2` before
creating an output directory. It does not emit a partially filtered feature table.
Missingness and censoring need an explicit design outside this complete-pair
example; do not discard devices until it passes.

## 5. Review warnings before downstream use

For the six measurement CLIs, exit `0` means all selected groups passed that
tool's documented checks; `1` means a report contains rejected/excluded groups;
`2` means a fatal CLI, schema, input or output error. The feature builder uses
`0` for completion and `2` for failure. None of these codes is a scientific
quality score. A warning may accompany exit `0`, and producing JSON is not proof
that every result is usable. The sweep audit also uses only `0` and `2`: plateaus,
singletons and coordinate reversals are retained and described, not exclusions.

| Report finding | Review action |
| --- | --- |
| Duplicate voltage/pair/state or nonmonotonic sweep | Check acquisition identity and source order. Do not deduplicate, sort or average away the conflict. |
| Target outside a branch, no current crossing, or bracket too wide | Reconsider the documented protocol/data coverage. Do not extrapolate or loosen a bound just to fill a cell. |
| Missing state, condition/bias mismatch, invalid resistance | Investigate the records. Keep exclusion reasons; do not impute a ratio or replace null with zero. RRAM diagnostic categories may overlap. |
| Ratio below one | Retain the value and flag; review state preparation and labeling without automatically swapping them. |
| TLM extrapolation, repeated gaps, or replicate warnings | Inspect sampled geometry, row weighting and residuals. High R-squared does not validate contacts, remove parasitics or bound intercept uncertainty. |
| Hall residuals, zero-current normalization or zero-field extrapolation warnings | Inspect the full branch and protocol. A slope/intercept is descriptive, not validated Hall physics or a corrected transport property. |
| Transfer log-slope selection, residual or polarity warning | Review all retained rows, window rationale and physical regime. A reciprocal is not a certified subthreshold swing; do not trim or retune just to improve the result. |
| Binary64 rounding warning | Retain exact original numeric text and inspect the specific issue. Printed JSON precision cannot establish measurement resolution. |

Save real reports deliberately: `--output` refuses an existing path, including a
symlink, and requests POSIX mode `0600`. The feature example requests directory
mode `0700`. Other platforms may differ. An output I/O failure can leave a partial
new file or directory; do not use it as a completed result. Choose a new destination
after investigating the failure. `--stdout` is an explicit alternative that can
expose raw IDs and measurements in terminal/CI logs. None of the tools sends data
externally, but you remain responsible for where reports and logs are retained.

Before modeling, define a separately sourced endpoint and when its information
becomes available. Keep related measurements in the same evaluation split at the
appropriate device, wafer, batch or campaign boundary. `split_group=batch` in
the example is a declared grouping choice, not evidence of batch independence.
Fit preprocessing only within training groups. IDs, repeat counts and condition
metadata are not automatically predictors; use the feature allowlist deliberately.
Predicting the ratio from the very resistances that define it is not evidence of
scientific predictive performance. Six invented device rows cannot support a
meaningful model evaluation, causal claim, endurance or lifetime estimate.

## 6. Recheck the software

```sh
python3 run_tests.py
```

The current suite has 359 tests: 45 I–V, 53 RRAM, 47 TLM, 51 current-criterion Vth,
50 Hall-response, 36 sweep-audit, 50 transfer-log-slope and 27 end-to-end feature tests.
The commands and checks in this guide were run
with Python 3.12.14 on Linux; other supported-source versions/platforms remain
unverified here. The tests cover software contracts and invented examples, not
instrument calibration or laboratory validity. Use each CLI's `--help` and linked
README for its full schema, numerical/resource bounds and rejection rules.
