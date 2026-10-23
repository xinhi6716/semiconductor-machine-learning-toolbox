# Constant-current threshold criterion extractor

For each explicitly labeled semiconductor transfer-sweep branch, find the
**signed gate-to-source voltage at a user-declared signed drain current**.
Return a measured exact hit or a locally interpolated value, with its adjacent
sample bracket and acquisition provenance. Forward and reverse branches remain
separate; the tool does not average away history-dependent differences.

This is a small measurement-preprocessing utility using only Python's standard
library. It has no network calls, dependencies, instrument control, private data,
or copied measurement results. The included fixture is entirely invented.

## Definition and scope

The constant-current extraction convention searches for VGS where ID equals a
specified target. Keithley's primary [ACS Programmer's Manual, Rev. F, section
4-16, PDF page 109](https://download.tek.com/manual/ACS-907-01_F_Nov_2017_Prog_ACS-907-01F.pdf#page=109)
describes this convention, its bias dependence and linear interpolation between
gate-voltage steps. The examples there use geometry-scaled targets; this tool
deliberately chooses **no default current or geometry scaling**. Only the signed
unnormalized terminal current explicitly supplied by the user is used. No source text,
figures or datasets are redistributed.

The output `gate_source_voltage_at_criterion_V` is a **criterion-defined** value,
often called constant-current threshold voltage. It is not a universal physical
threshold, an intrinsic material constant, an independent estimate of strong
inversion, a switching guarantee, or evidence of a microscopic mechanism.
Different criteria, drain/body bias, temperature and acquisition history may
produce different values. This tool neither identifies nor corrects their causes.

For adjacent samples `(V1, I1)` and `(V2, I2)` strictly bracketing `Ic`:

```text
t = (Ic - I1) / (I2 - I1)
VGS_at_criterion = V1 + t * (V2 - V1)
```

This is **linear current**, not log-current interpolation or extrapolation.
The samples retain acquisition order even on descending sweeps. A value equal
to the criterion at exactly one measured sample uses that sample without
interpolation. Computed digits and a small bracket do not establish accuracy,
resolution, uncertainty or calibration. No 1 mV accuracy is claimed.

The required `--accept-transfer-assumptions` flag declares, but does not verify:

- All three voltage columns are source-referenced VGS, VDS and VBS, not absolute
  instrument-output voltages relative to an unrelated ground.
- Each group is one uninterrupted, strictly monotonic VGS sweep with fixed
  nonzero VDS, fixed VBS, positive temperature in kelvin, and fixed timing,
  preconditioning, state and measurement protocol. Labels do not prove stability.
- Drain current uses one declared sign convention. `positive` and `negative`
  describe the current sign, **not** n-channel/p-channel identification. VGS,
  VDS and VBS remain signed and are not inferred from that declaration.
- The user has chosen a meaningful current criterion for the intended protocol
  and has evaluated compliance, noise, leakage, settling and contact effects.
  The tool cannot certify a linear/saturation regime or suitability of the data.

There is no automatic W/L, width, area or geometry normalization. Do not put
current density in the current column or interpret the criterion as A per square.
If a protocol specifies a geometry-scaled target, calculate and document the
actual current externally using the correct declared geometry, then supply that
current. Devices needing different criteria require separate runs. Geometry is
not inspected or certified by this tool.

## Input schema and grouping

UTF-8 CSV, optionally BOM-prefixed, with exactly these unique columns in any
order:

```csv
lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current
invented-lot,invented-wafer,invented-fet,dc-bias,r1,forward,300,0.05,0,0.4,0.2
invented-lot,invented-wafer,invented-fet,dc-bias,r1,forward,300,0.05,0,0.8,1.8
```

- The first six fields together identify one extraction group. `device` names
  one physical device, `replicate` a separate acquisition, and `branch` one
  monotonic segment, such as `forward` or `reverse`. Separate cycles must have
  separate replicate IDs. Do not reuse the entire key to combine acquisitions.
- `condition` links to the original record of timing, preconditioning, ambient,
  instrument configuration and other relevant settings. Retain that record.
  Missing provenance should use an explicit placeholder and remain a limitation.
- `temperature_K` is explicitly in kelvin and must be positive. `drain_voltage`
  means VDS, `body_voltage` means VBS, and `gate_voltage` means VGS, all in the
  required `--voltage-unit`: `V`, `mV`, or `uV`.
- `drain_current` uses the required `--current-unit`: `A`, `mA`, `uA`, `nA`, or
  `pA`. `u` denotes the micro prefix. All nonzero currents must agree with
  `--current-polarity`; zero off-state samples are allowed. Currents are never
  converted to absolute values. Negative-current data need a negative criterion.
- Temperature, VDS and VBS must be **exactly equal after exact unit conversion**
  within a group. VDS must be nonzero. No averaging or tolerance is applied.
  These are deliberately conservative software checks, not instrument specs.
- Groups may be interleaved in the file; their within-group acquisition order
  is preserved. No sorting repairs a sweep. Every group needs at least two rows,
  unique VGS values, and strictly increasing or strictly decreasing VGS.

Identifiers must be nonempty, unpadded, at most 128 characters, and contain no
ASCII control characters. Extra columns are not silently discarded. Empty
physical lines are skipped; whitespace-only and delimiter-only rows are invalid.
Malformed CSV, invalid numbers/identifiers and exceeded resource bounds are
fatal to the entire input, including malformed groups excluded by a branch filter.

## Run the invented example

Python 3.10+ source; locally executed with Python 3.12 on Linux. From the root:

```sh
python3 tools/vth-constant-current/vth_constant_current.py \
  tools/vth-constant-current/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit uA \
  --criterion-current 1 --criterion-current-unit uA \
  --current-polarity positive --accept-transfer-assumptions --stdout

python3 -m unittest discover -s tools/vth-constant-current/tests -v
```

The fixture yields `0.6 V` for `forward` and `0.7 V` for `reverse`, each at
`1 uA`, with different brackets. These are invented software-test results, not
device-performance measurements or proof of a hysteresis mechanism. No combined
value or hysteresis statistic is calculated.

Options:

- `--criterion-current` is required, signed and nonzero; its own explicit
  `--criterion-current-unit` may differ from the input current unit. For negative
  values use, for example, `--criterion-current=-1 --current-polarity negative`.
- `--branch NAME` selects an exact branch label across groups. All branches are
  processed independently when omitted. No match is fatal.
- `--max-bracket-span VALUE` optionally caps the absolute VGS span of an
  interpolation bracket in `--voltage-unit`. Equality is accepted. An exact
  measured hit has zero span. This is a user-chosen spacing bound, not an error
  bar or a scientific quality threshold; no default bound is invented.
- Replace `--stdout` with `--output NEW_PATH.json` to create a report file.
  A destination is mandatory. Stdout deliberately reveals raw identifiers,
  normalized observations and extracted values. Use it only in an appropriate
  terminal/logging environment.

Output uses exclusive creation with requested POSIX mode `0600`. Existing files,
directories and symlinks, including dangling symlinks, are never overwritten.
Other operating systems may interpret permissions differently. An output-write
failure can leave an incomplete **new** file; discard it after a fatal exit.
The input is not modified. Nothing is sent to any external service.

Exit status is `0` if every selected group passes the documented software checks,
`1` if a report is produced with at least one rejected group, or `2` for a fatal
CLI/input/output error. A `0` is not a scientific validity or metrology pass.
Fatal diagnostics do not include raw private values, identifiers or paths.

## Crossing checks and rejection

No sample is dropped to rescue an extraction. After checking conditions, current
polarity and strict VGS acquisition order:

1. Count exact criterion hits and strictly interior crossings between adjacent
   measured samples. An isolated exact hit counts once, not once per neighbor.
2. More than one crossing rejects the group as `nonunique_current_crossing`.
   This includes two samples at the criterion, a flat segment at the criterion,
   an exact hit plus another crossing, or multiple interior crossings.
3. No crossing rejects it as `criterion_not_bracketed`. No nearest-point fallback,
   tolerance matching or extrapolation is provided.
4. Require the entire signed current sequence to be nondecreasing or
   nonincreasing. A unique crossing on an otherwise nonmonotonic current trace
   rejects as `nonmonotonic_drain_current`. Flat regions away from the criterion
   are allowed. Small noise reversals are not silently smoothed; this conservative
   tool may reject usable experimental data that require a different protocol.
5. For one interior crossing, enforce the optional bracket span and interpolate.
   Exact endpoint hits are accepted when the full branch meets the other checks.

Other rejection reasons are `fewer_than_two_samples`, `nonpositive_temperature`,
`zero_drain_bias`, `inconsistent_temperature_or_bias`,
`current_sign_conflicts_with_declared_polarity`, `duplicate_gate_voltage`,
`nonmonotonic_gate_voltage`, `bracket_span_exceeds_limit`, and
`derived_numeric_range_error`. Rejected groups have `extraction: null` and keep
their observations; they never retain a stale extracted value. If several checks
fail, the first applicable check is reported, not an exhaustive fault diagnosis.

## Report, numerical bounds and tests

JSON schema version 1 includes original group IDs; input/selected/rejected counts;
input SHA-256; source-row numbers (the last physical line of a CSV record, header
included); normalized per-row observations; declared signed criterion and units;
exact stripped CLI numeric strings; acquisition direction; fixed conditions;
and warnings about interpretation. The input path is omitted. Keep the original
CSV to recover exact decimals, since its fingerprint alone does not contain data.

An accepted `extraction` gives the signed criterion-defined VGS, method, criterion,
bracket width, bracket voltages/currents, source rows and fraction from the first
to second acquisition-ordered sample. A measured exact hit reports one source
row and a null fraction. Separate branches and replicates are never pooled, and
are not assumed to represent statistically independent devices. No population
means, confidence intervals, uncertainty estimates or causal claims are made.

Numeric syntax is ordinary decimal/scientific notation with optional surrounding
whitespace, at most 64 stripped characters and 32 Decimal coefficient digits.
The parsed Decimal exponent must lie from -200 through 200. After unit conversion,
every nonzero numeric input must have magnitude from `1e-150` through `1e150`.
NaN, infinity, hexadecimal and underscore forms are rejected. Syntax and bounds
are checked before creating large rational integers.

Only regular files are read, using a nonblocking open to avoid FIFO hangs.
Limits are 8 MiB input, 10,000 rows, 1,000 groups and 1,000 rows per group. These
are resource bounds, not meaningful laboratory limits. Exact rational arithmetic
handles unit conversion, equality, ordering and interpolation without binary64
cancellation. Final JSON numbers are rounded binary64. Nonzero derived physical
values that underflow, and any overflow, reject the group rather than printing
zero or infinity. The dimensionless interpolation fraction is an exception: a
strictly interior exact fraction may round to `0` or `1`, with the warning
`binary64_rounding_hides_interior_interpolation_fraction`. The threshold is still
computed with the exact fraction, not this rounded diagnostic, so reversing the
same bracket does not change whether its physical result is representable.
An endpoint-rounded fraction does not turn an interpolation into a measured hit.
Distinct exact input gate voltages/currents that collapse to the same JSON number carry
explicit rounding warnings; an interpolated criterion that rounds to a bracket
endpoint also warns. These warnings do not repair inadequate measurement
resolution, and JSON numbers alone may not reproduce exact-decimal decisions.

Tests use invented data for exact/interpolated crossings, signed negative
current, all unit combinations, forward/reverse separation, every grouping key,
condition changes, ambiguity and nonmonotonicity, exact-decimal boundary behavior,
resource limits, malformed input, private diagnostics and no-overwrite output.
Passing them establishes tested software behavior only, not validated device
physics or laboratory measurement accuracy.
