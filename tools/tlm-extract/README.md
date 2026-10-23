# Linear TLM Extractor

Fit measured resistance versus contact gap for a **conventional rectangular
transfer-length-method (TLM) structure**, separately for each declared structure,
condition and replicate. Return channel sheet resistance and the **per-contact**
resistance under explicit equal-contact assumptions.

This is a semiconductor measurement-preprocessing tool, not an Ohmic-contact
detector, instrument driver, nonlinear contact model, or validated metrology
package. It uses Python's standard library, has no network calls, and never
changes the input. All included data are original, invented software fixtures.

## Model and interpretation

For a uniform-width channel with equivalent contacts:

```text
R_total(d) = R_sheet * d / W + 2 * R_contact
fit: R_total(d) = slope * d + intercept
R_sheet   = slope * W       [ohm/square]
R_contact = intercept / 2   [ohm, one contact, not the pair]
```

Here `d` is the **edge-to-edge channel gap**, not the center-to-center pitch,
contact length, or distance between probe tips. `W` is the channel/contact width
transverse to current. The sheet value describes the channel between contacts,
not automatically the material under the metal.

The measurement relation and the factor of two are described in Hajzus et al.,
*Contacts to solution-synthesized SnS nanoribbons: Dependence of barrier height on
metal work function*, equations 8 and 11 and the TLM section, pages 7 and 9–10 of
this [NIST-hosted primary research manuscript](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=924311).
That paper also explains why contact-length assumptions affect transfer-length
and specific-contact-resistivity extraction. This tool deliberately **does not
calculate either quantity**. No source data or figures are redistributed.

The required `--accept-linear-tlm-assumptions` flag declares that the input is
intended for this model. It is not software verification of any assumption:

- Contacts are Ohmic over the measured range, have equivalent resistance and
  geometry, and do not vary systematically between pairs or with gap.
- Channel sheet resistance and width are uniform within each group. The intended
  current path is rectangular; circular TLM, current-spreading corrections and
  nonuniform geometries are unsupported.
- Bias, temperature, state, orientation, timing and acquisition protocol are fixed
  within the named condition. A label cannot prove these matched in the lab.
- Probe, lead, metal and other series contributions are negligible or already
  removed. A constant parasitic term is indistinguishable from contact resistance
  in the intercept; the tool does not subtract it or isolate it automatically.
- Gap errors are ignored by ordinary least squares. This is not an errors-in-
  variables fit, uncertainty propagation, or independent-sample analysis.

If these conditions cannot be defended, do not interpret the physical estimates
as isolated contact or material properties. A positive fit or high R-squared
cannot establish them.

## Input schema

UTF-8 CSV, optionally with a BOM. Exactly these nine unique columns are required;
column order may differ:

```csv
lot,wafer,structure,condition,replicate,pair,spacing,width,resistance
synthetic-lot,synthetic-wafer,bar-A,fixed-condition,r1,p1,2,10,30
synthetic-lot,synthetic-wafer,bar-A,fixed-condition,r1,p2,5,10,60
synthetic-lot,synthetic-wafer,bar-A,fixed-condition,r1,p3,10,10,110
```

- `lot`, `wafer`, `structure`, `condition`, `replicate` together define one fit.
  `structure` names one physical TLM structure; `replicate` names one separate
  acquisition of it. Never reuse the same key to pool independent structures or
  changing conditions. If provenance is unavailable, use a clearly declared
  placeholder, retain that limitation, and do not infer independence from it.
- `pair` identifies one measured contact pair within the group. Duplicate pair
  IDs reject that group, including duplicate identical observations. Put repeat
  acquisitions in separate replicate groups, each with enough gaps for a fit.
  Distinct pairs may share a contact; unique IDs do not establish independence.
- `spacing` and `width` use the same explicit `--length-unit`: `m`, `mm`, `um`,
  or `nm`. The spelling `um` denotes micrometres. Both must be positive.
- `resistance` is an already-determined total pair resistance, in explicit
  `--resistance-unit`: `ohm`, `kohm`, or `Mohm`. It must be positive. The tool
  does not extract resistance from an I–V trace or infer an appropriate bias.
- Each group needs **at least three distinct gaps** and exactly equal declared
  widths after exact unit normalization. A width mismatch rejects the group;
  no tolerance, averaging or silent pooling is applied.

Identifiers must be nonempty, at most 128 characters, unpadded and free of ASCII
control characters. No extra columns are silently discarded. Keep the detailed
measurement protocol in the original record associated with `condition`.
Entirely blank physical lines are skipped; whitespace-only and delimiter-only
rows are invalid. Malformed CSV, invalid identifiers or unparseable numbers are
fatal for the whole input rather than partially loading an unknown dataset.

## Run

Python 3.10+ source; only Python 3.12 on Linux has been executed locally. From the
repository root:

```sh
python3 tools/tlm-extract/tlm_extract.py \
  tools/tlm-extract/examples/synthetic_tlm.csv \
  --length-unit um --resistance-unit ohm \
  --accept-linear-tlm-assumptions --stdout

python3 -m unittest discover -s tools/tlm-extract/tests -v
```

The synthetic example exits `0`: both replicates give `100 ohm/square`, with
`5 ohm` and `6 ohm` per contact respectively. Those invented values are not a
real-device result or evidence of independent-device variation.

Replace `--stdout` with `--output NEW_PATH.json` to create a report file. Choosing
`--stdout` explicitly prints identifiers, normalized observations and derived
values. No destination is selected by default. Output files use exclusive create
with requested POSIX mode `0600`; existing files, directories and symlinks are
never overwritten. Other platforms may implement permissions differently. A
write failure can leave an incomplete **new** file; discard it after a fatal
exit. Nothing is transmitted to an external service.

Exit status:

- `0`: every group yielded finite, nonnegative-contact/positive-sheet estimates
  under the declared model. This is not a metrology quality pass.
- `1`: JSON was produced but one or more groups were rejected. Other groups may
  still be usable; inspect `counts` and each `status`.
- `2`: fatal CLI, input or output error. No usable report is promised. Diagnostics
  avoid raw private identifiers, numeric values and paths.

## Fitting and reports

Every row is weighted equally in **unweighted ordinary least squares with an
intercept**. Repeated gaps with different pair IDs are accepted, flagged, and
weighted as separate rows; gap means are not substituted. Thus an uneven number
of rows per gap changes its influence. Rows are retained in acquisition order,
although the fit itself is order-independent. There is no outlier deletion,
robust regression, baseline subtraction, nonnegative constraint or automatic
refitting.

JSON schema version 1 includes:

- Original group IDs, row counts, rejection counts, normalized observations,
  pair IDs, and physical source-row numbers (the final line of each CSV record,
  including the header). The exact input bytes have a SHA-256 fingerprint; the
  report omits the input path. Retain the input to reproduce its exact decimals.
- `fit.slope_ohm_per_m`, `fit.intercept_ohm`, R-squared, descriptive RMSE
  (`sqrt(sum(residual^2) / n)`, **not** residual standard error), maximum absolute
  residual, width, gap range, and extrapolation ratio. Residuals are observed
  minus fitted resistance. R-squared is `null` for a constant response.
- `physical_estimates.sheet_resistance_ohm_per_square` and
  `physical_estimates.per_contact_resistance_ohm` only for accepted fits.
- A negative intercept or nonpositive slope is a `nonphysical_fit` rejection.
  **Signed fit coefficients remain available**, but `physical_estimates` is
  `null`. The software never clips or takes absolute values to create a positive
  result. Zero intercept is retained with a warning, not evidence of zero actual
  contact resistance. No arbitrary R-squared cutoff is used.
- `zero_spacing_extrapolation_ratio = min(gap) / (max(gap) - min(gap))` describes
  the distance from the sampled range to zero gap, relative to that range.
  Every fit warns about extrapolation; a ratio at least 10 additionally warns
  `large_zero_spacing_extrapolation`. Ten is a transparent software heuristic,
  not a scientifically validated acceptance threshold. Even a smaller ratio
  does not bound intercept uncertainty.
- Every fit warns that rows are not assumed independent. Separate replicate
  groups of the same structure and condition also receive an explicit warning.
  No population means, standard errors, confidence intervals, p-values, device
  counts or reliability claims are inferred from labels or repeated rows.

Group rejection reasons include duplicate pair IDs, nonpositive geometry or
resistance, inconsistent width, fewer than three distinct spacings, nonphysical
fit coefficients and derived values outside finite numeric range. A group's
invalid observation is not dropped to rescue its fit.

## Numerical and resource limits

Numeric fields accept ordinary decimal/scientific notation, optional surrounding
whitespace, at most 64 stripped characters and 32 Decimal coefficient digits.
The parsed Decimal exponent must lie between -200 and 200. Every nonzero value,
**after unit conversion**, must have absolute magnitude from `1e-150` through
`1e150`. NaN, infinity, hexadecimal and underscore forms are rejected. Decimal
syntax and bounds are checked before constructing potentially large integers.

Input is limited to a regular file of 8 MiB, 10,000 measurement rows, 1,000 groups,
and 1,000 rows per group. These are resource bounds, not instrument specifications.

Decimal input, unit conversion and regression arithmetic use exact rational
numbers, avoiding cancellation in the normal equations. Square-root diagnostics
use a 50-digit Decimal intermediate. Final JSON numbers are rounded binary64;
overflow and nonzero underflow cause a rejection rather than Infinity or zero.
If distinct exact gaps round to the same JSON number, the group carries
`binary64_spacing_rounding_merges_distinct_inputs`. Exact source input and the
reported distinct count remain important for reproduction. Exact arithmetic
cannot compensate for poor experimental gap resolution, uncertain geometry,
noise, parasitic resistance, or unstable extrapolation.

Tests cover analytical lines and noisy residuals, all 12 unit combinations,
coefficient signs, grouping/replicates, exact-width and near-equal-gap handling,
resource and numerical boundaries, malformed input, private diagnostics and
no-overwrite output. Passing them establishes only tested software behavior,
not physical-model validity, calibrated uncertainty or real-device performance.
