# Rust CLI reference adaptation

> Windows permissions inherit directory ACLs; POSIX mode 0600 claims are Unix-only. Windows runtime testing is not claimed.
>
> Native usage is `smlt hall-response`. Any Python API, unittest, or generator below is explicitly reference-only validation under `reference/python/` in the standalone source archive (from a Git checkout, replace that prefix with `../`); it is not called by the Rust executable and is not required to use it. For native tests use `cargo test --locked`; for ready-made native examples use `scripts/run-demo.sh` or `scripts/run-demo.ps1`.

Native command: `smlt hall-response`. Source schema and scientific limitations below are preserved from the Python reference; Python API examples are legacy reference interfaces, not Rust APIs. All bundled data are synthetic software fixtures.

# Hall Response Extractor

A small, standard-library-only extractor for **signed transverse voltage versus
signed magnetic flux density** at a fixed declared bias current and temperature.
It reports one descriptive linear fit per physical-device/condition/replicate/
branch, its zero-field intercept, residual diagnostics and, for nonzero current,
a signed current-normalized apparent Hall sensitivity.

This is measurement preprocessing, not a Hall-physics validator or a trained ML
model. It does not infer carrier density, carrier type, Hall coefficient, mobility,
thickness or resistivity. Included data are original invented software fixtures,
not laboratory measurements or evidence of device performance.

## Run

Python 3.10+; no installation, API key, service, instrument or network connection.
From the repository root:

```sh
smlt hall-response \
  examples/hall-response/synthetic_hall.csv \
  --field-unit T --voltage-unit mV --current-unit mA \
  --accept-hall-assumptions --stdout

python3 -m unittest discover -s reference/python/tools/hall-response/tests -v
```

For a private report replace `--stdout` with `--output NEW_PATH.json`. A destination
is mandatory: printing identifiers and measurements must be deliberate. Output
files use exclusive creation, request POSIX mode `0600`, and never overwrite an
existing file or symlink. Permissions elsewhere may differ. A failed write can
leave a partial new file; discard it after a fatal exit. Inputs are never changed.

## CSV and declarations

The header must contain exactly these columns, in any order:

```csv
lot,wafer,device,condition,replicate,branch,temperature_K,bias_current,magnetic_field,transverse_voltage
```

- `(lot, wafer, device)` identifies a physical device. The entire first six-column
  key defines a fit. Different conditions, replicates and branches stay separate;
  multiple groups do not imply multiple independent physical devices.
- `condition` identifies an externally documented, unchanged measurement protocol:
  current-contact identities and positive current direction; ordered transverse
  voltage contacts (`V_plus - V_minus`); positive field axis perpendicular to the
  sample; device state; settling, temperature control, calibration and any prior
  processing. Use different IDs if these change. Do not combine swapped leads,
  field orientations or current polarities within one branch.
- `branch` identifies one acquisition-ordered, strictly increasing or strictly
  decreasing field segment. Preserve original order, including when groups are
  interleaved. At least three distinct field values are required. Duplicate fields
  and reversals reject the group: split repeated sweeps or dwell records into
  justified branches/replicates upstream, not by blindly sorting the data.
- `temperature_K` is positive kelvin and must be exactly constant within a group.
  No Celsius conversion is inferred. `bias_current` is signed, declared terminal
  current and must also match exactly within a group after conversion.
- `magnetic_field` is signed magnetic flux density **B**, not magnetic field
  strength H. Supported units: `T`, `mT`, `uT`, `G`, `kG`; 1 G = 0.0001 T.
- `transverse_voltage` units: `V`, `mV`, `uV`, `nV`. Current units: `A`, `mA`, `uA`,
  `nA`, `pA`. All unit flags are required, case-sensitive and file-wide. Numeric
  signs are retained; no absolute-value transform or polarity inference occurs.
- A zero-current group is allowed only as a transverse-voltage background
  diagnostic. It gets a null current-normalized sensitivity and a warning. It is
  not interpreted as a current-driven Hall measurement.

`--accept-hall-assumptions` declares this context. The software checks declarations,
not what happened in the laboratory. The program cannot determine actual terminal
mapping, contact quality, uniformity, alignment, compliance, illumination, thermal
stability or calibration from the CSV. A positive or negative slope is never
converted to an electron/hole label.

A `condition` ID is provenance, not a replacement for retaining the actual
measurement protocol. The input hash and physical source-line numbers preserve
lineage, but do not certify the protocol or the truth of a device ID.

## Calculations and interpretation

Within each group the unweighted least-squares relation is:

```text
V_transverse = a * B + b
S_I = a / I_declared                    (only for nonzero I_declared)
residual_i = V_i - (a * B_i + b)
RMSE = sqrt(sum(residual_i^2) / N)
R_squared = 1 - sum(residual_i^2) / sum((V_i - mean(V))^2)
```

- `a`, reported as `fit.slope_V_per_T`, is a signed voltage-versus-field response
  over the measured range. A negative slope is a valid descriptive result.
- `S_I`, `current_normalized_sensitivity_V_per_A_T`, preserves the sign of both
  slope and current. Its units are V/(A T), not those of a bulk Hall coefficient.
  It remains an apparent response, potentially including non-Hall contributions.
- `b`, `fit.zero_field_intercept_V`, is the fitted value at zero field, not an
  isolated instrument offset. It is marked as extrapolated if zero lies outside
  the sampled range, or as not directly sampled when zero is inside the range
  without an exact zero-field observation. A measured zero point need not equal
  the fitted intercept. No intercept is subtracted from input data.
- The zero-field extrapolation ratio is distance from zero to the sampled field
  interval divided by its width, or zero inside that interval. Ratios of 10 or
  more receive an additional descriptive warning; 10 is a software heuristic,
  not a metrology acceptance threshold.
- Each observation includes the fitted voltage and residual. RMSE uses N rather
  than N-2. `max_abs_residual_over_voltage_span` divides the largest absolute
  residual by the measured peak-to-peak voltage span. It is null for constant
  voltage, as is R-squared. Neither is a measurement uncertainty or confidence
  interval. Field values are treated as exact and no weights are inferred.

Every nonzero exact residual triggers a possible-nonlinearity/noise/drift warning.
This deliberately has no hidden significance threshold: decimal rounding alone
can cause it. Nonlinearity, noise and drift are not distinguished. Zero residuals
also carry a warning that they do not validate linearity. Three or more points
allow residual diagnostics, but do not guarantee adequate sampling or precision.
High R-squared, small residuals, or either slope sign cannot validate a physical
Hall model or rule out systematic contamination.

Only full-branch fits are supported: no field-window selection, outlier deletion,
smoothing, automatic sorting, pooling, current reversal, antisymmetrization or
opposite-field matching. Even if both field signs occur, they are not implicitly
paired and no offset cancellation is claimed. An independent, explicitly justified
measurement protocol is needed before extracting material transport properties.
Do not use these results to extrapolate a sensor calibration beyond the data.

## Output and rejection

JSON schema version 1 includes input units, SHA-256, source-row convention,
assumptions, group counts, references and per-group results. Observations are
reported in acquisition order with normalized SI values even for rejected groups.
`distinct_physical_device_ids` counts unique `(lot, wafer, device)` labels, not
verified independent specimens. There is no across-group statistic or merging.

- Exit **0**: all groups have descriptive fits, possibly with warnings. This is
  not a scientific quality, linearity or Hall-validity rating.
- Exit **1**: at least one group rejected, with a complete deliberate JSON report.
  Reasons include nonpositive temperature, changed temperature/current, repeated
  fields, fewer than three distinct fields, field reversals or unrepresentable
  derived values. Rejected groups have null fit and sensitivity, not zero values.
- Exit **2**: fatal schema, numeric, assumption, CLI, read or write error. Errors
  deliberately omit raw identifiers, measurements, paths and argument values.

CSV must be a regular UTF-8 file (a BOM is allowed). Empty lines are skipped;
whitespace-only records and missing/extra/duplicate columns are not repaired.
Identifiers must be nonempty, unpadded, control-free and at most 128 characters.
Numeric syntax is signed decimal/scientific notation; NaN, infinity, underscores
and unit-suffixed values are rejected. Numeric text is at most 64 characters,
32 significant decimal digits and absolute stored decimal exponent 200. Nonzero
SI input magnitudes must be between 1e-150 and 1e150 inclusive. Limits: 8 MiB,
10,000 measurement rows, 1,000 groups and 1,000 points per group.

Exact decimal/rational arithmetic avoids binary64 cancellation in regression and
condition matching. JSON numbers are binary64 approximations: warnings identify
field or voltage distinctions lost on conversion. Nonzero derived numbers that
would become infinity or zero reject the entire group, without a partial fit.
This numerical range is a software bound, not a claim of physical plausibility.

## Invented examples and verification

`reference/python/tools/hall-response/examples/generate_synthetic.py` prints the checked-in CSV deterministically using
only original algebraic fixtures. Units are T, mV, mA and K. There are 20 points,
four groups and two physical-device labels:

- Invented A, r1, up: slope 0.002 V/T, intercept 0.0003 V, S_I = 2 V/(A T).
- Invented A, r1, down: same slope/S_I, intercept 0.0004 V, retained separately.
- Invented A, r2, up: current -0.001 A and slope -0.002 V/T, S_I = 2 V/(A T).
- Invented B, r1, up: deliberately curved voltage; slope 0.001 V/T, intercept
  0.0002 V, maximum absolute residual 0.0001 V and a nonzero-residual warning.

The generator prints to stdout; save to a new path when reproducing it. Tests
cover signed/unit conversions, conditions and grouping, exact/numerically delicate
fits, constant/curved response, schema/resource limits, input privacy and exclusive
output. Passing them demonstrates software behavior, not experimental validity.

## Primary references and scope boundary

Checked 2026-10-23. These sources motivate the measurement interpretation; the
synthetic data, code, diagnostic choices and output format are original.

1. [NIST, The Hall Effect](https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/hall-effect):
   describes transverse Hall voltage under fixed current and perpendicular B;
   material-property extraction needs additional assumptions and quantities.
2. [NIST, Resistivity and Hall Measurements](https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/resistivity-and-hall):
   distinguishes signed terminal/field conventions and a deliberate reversal
   protocol for offset handling. This tool does not implement that protocol.
3. [NIST, Hall Effect Measurements Sources of Error](https://www.nist.gov/pml/nanoscale-device-characterization-division/popular-links/hall-effect/resistivity-and-hall/hall):
   flags contact, settling and thermal issues that a numerical fit cannot settle.
   NIST marks these tutorial pages as archival.
4. [Lake Shore, Hall Sensor Application Guide, revision G](https://www.lakeshore.com/docs/default-source/product-downloads/manuals/hall_sensor-_manual.pdf),
   section 2.2: relates sensor voltage to field and angle, defining magnetic
   sensitivity at fixed current. Our signed slope/current normalization is an
   operational feature definition, not a copied calibration or device guarantee.
