# User-Windowed Transfer Log-Slope

Fit log10 of explicitly declared-polarity drain current versus signed VGS in
one **user-chosen inclusive voltage interval**, separately for every declared
transfer branch. Report signed slope, residuals and a clearly labeled reciprocal.
This is useful preparation for subthreshold-swing analysis, but the program
**never identifies or certifies a subthreshold window**. A fit, high R-squared,
positive reciprocal or successful exit does not establish that interpretation.

This fills a different need from the [constant-current tool](../vth-constant-current/README.md):
that tool finds VGS at a chosen signed current using adjacent linear-current
interpolation; this tool fits all log-current observations inside chosen voltage
bounds. It does not extract a threshold or combine the two methods automatically.

Python 3.10+; standard library only. No dependencies, network, instrument control,
external data, model training, downloaded code or experimental-performance claims.

## Quick start

From the repository root:

```sh
python3 tools/transfer-log-slope/transfer_log_slope.py \
  tools/transfer-log-slope/examples/synthetic_transfer.csv \
  --voltage-unit V --current-unit nA --current-polarity positive \
  --window-min 0.1 --window-max 0.4 \
  --accept-log-transfer-assumptions --stdout
```

The original invented fixture has 24 rows, three physical-device IDs and four
branches. Sixteen samples are inside the window; all eight outside samples remain
in the report. The two branches of invented A each give `10 decades/V` and a
signed reciprocal `100 mV/decade`, with different intercepts. Invented B is curved
and retains residuals. Invented C gives `-10 decades/V`, signed reciprocal
`-100 mV/decade` and separately labeled magnitude `100 mV/decade`. Exit is `0`;
these are arithmetic examples, not measured subthreshold swings or device models.

Use `--output NEW_REPORT.json` instead of `--stdout` to create a new report. Files
are opened exclusively with requested POSIX mode `0600`; existing files, the
input itself and live/dangling symlinks are never overwritten. Other platforms
may differ. A write failure can leave an incomplete new file: discard that file
rather than treating it as a report. `--stdout` deliberately exposes raw IDs,
input numeric text, measurements and derived values. Errors omit raw values,
identifiers and paths. Inputs are not modified or transmitted anywhere.

## Input contract

Exactly these columns, in any order, with case-sensitive names and no extras:

```csv
lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current
```

- The first six columns are exact, nonempty, unpadded, control-free IDs of at most
  128 characters. Each key denotes one acquisition-ordered branch. Groups may be
  interleaved, but their supplied per-group order is retained. Do not rename
  repeated acquisitions into new devices; physical-device counts use lot/wafer/device.
- `gate_voltage`, `drain_voltage`, `body_voltage` mean signed source-referenced
  VGS, VDS, VBS. `--voltage-unit` is explicitly `V`, `mV` or `uV`, shared by all
  three columns **and both window bounds**. VDS must be nonzero and fixed within
  the whole branch; VBS is fixed. A branch must have at least three strictly
  increasing or decreasing VGS samples with no duplicates anywhere in the branch.
- `temperature_K` is positive, fixed kelvin temperature for the whole branch.
  The condition ID must refer to separately documented fixed device state,
  timing, settling, instrument/compliance settings and terminal convention.
  The program checks declarations, not the actual laboratory conditions.
- `drain_current` is unnormalized signed terminal current. `--current-unit` is
  explicitly `A`, `mA`, `uA`, `nA` or `pA`. There is no W/L, width, area, current
  density, leakage subtraction or background correction.
- `--current-polarity positive` declares `s = +1`; `negative` declares `s = -1`.
  Selected current must satisfy `s * ID > 0`. This is a sign convention, not an
  inferred n-/p-channel device type. Negative-polarity data are transformed by
  their explicit declaration, never silently rescued with absolute values.
- UTF-8 CSV optionally starts with a BOM. Truly empty lines are skipped; a
  whitespace-only row is not empty. Quoted records are supported. `source_row`
  is the physical line where the CSV record ends, including the header. Every
  parsed row retains its original numeric cell text alongside SI values.
- Decimal/scientific syntax only, at most 64 characters after stripping numeric
  padding, 32 coefficient digits and decimal exponent magnitude at most 200.
  Each nonzero SI input or window bound has magnitude from `1e-150` to `1e150`,
  inclusive. Zero is permitted syntactically; semantic checks still apply.
  Blank numbers, NaN, infinity, unit suffixes and out-of-range values are fatal,
  including outside the window. No missing values are filled.
- Resource bounds: 8 MiB input, 10,000 measurement rows, 1,000 groups and 1,000
  rows per group. Only regular input files are accepted; FIFOs do not block.

## Window selection and rejection audit

Both `--window-min` and `--window-max` are required, with minimum strictly below
maximum. The same bounds apply to every group. Negative CLI values can use
`--window-min=-0.4`. There is no branch filter, current range filter, default
window, sliding/best-window search, automatic trimming or threshold-based choice.

Exact decimal/rational comparisons select every measured point satisfying
`window_min <= VGS <= window_max`. There is no interpolation at either bound,
no virtual sample and no fit outside the selected measured points. If bounds
extend beyond a branch, the actual selected range is reported with a warning.
It need not cover the entire requested interval. A group with fewer than three
selected samples is rejected rather than extending the window or borrowing rows.

Duplicate/reversing VGS, inconsistent bias/temperature or invalid branch
conditions reject the whole group, even if the conflict is outside the window.
A zero or polarity-conflicting **selected** current also rejects the whole group;
it is never dropped so the remaining points can pass. Finite zero/wrong-sign
currents **outside** the window are retained, labeled as non-loggable and not
fitted, with a warning. Nonmonotonic selected current is retained with a warning,
not sorted, smoothed, trimmed or rejected into a more attractive fit.

Every result contains all `observations`, `source_rows`, `selected_source_rows`
and `outside_window_source_rows`. Every observation has `window_selection`,
`current_log_domain` and `used_in_fit`; rejected groups have no fitted rows.
The input SHA-256, exact input window strings and source-row convention support
reproduction. Retain the original input and the rationale for choosing the
window: neither the hash nor the software can detect prior cherry-picking.
Groups remain separate; there is no averaged slope or device-population claim.

## Defined calculation

With selected signed current ID converted to amperes and VGS converted to volts:

```text
y_i = log10(s * ID_i / 1 A)
x_i = VGS_i
b = sum((x_i - mean(x)) * (y_i - mean(y))) / sum((x_i - mean(x))^2)
a = mean(y) - b * mean(x)
y_fitted_i = mean(y) + b * (x_i - mean(x))
r_i = y_i - y_fitted_i
RMSE = sqrt(sum(r_i^2) / N)
R_squared = 1 - sum(r_i^2) / sum((y_i - mean(y))^2)
```

Every selected row has equal weight, and VGS is treated as exact. The 1 A
reference makes the logarithm dimensionless and fixes the intercept convention;
changing the input current unit after correct conversion does not change the
fit. `slope_decades_per_V` is signed `b`, not an mV/decade quantity.

For nonzero `b`, `reciprocal_slope` provides signed `1/b` in V/decade, signed
`1000/b` in mV/decade and separately labeled `abs(1000/b)` in mV/decade. Current
sign and slope sign are different: negative current declared negative can still
have either slope sign, and reversing acquisition order does not reverse b.
For zero b the reciprocal is null. For constant log-current R-squared is null.
A nonconstant trace can also have zero b; its residuals and defined R-squared
are still reported. No reciprocal is obtained by fitting x on y.

The fit reports selected voltage/current ranges, voltage span and observed
log-current span; all selected rows receive fitted log-current and residuals in
decades. The zero-gate intercept may be an extrapolated diagnostic and is labeled
when zero lies outside the measured selected range. No current is predicted by
exponentiating the fit. RMSE uses N, not N-2, and is not measurement uncertainty.
There is no confidence interval, uncertainty propagation, significance test,
minimum decade-span claim, tolerance-based quality acceptance or physical limit.

Numeric inputs and selection are exact rationals. Logarithms use Python Decimal
base-10 evaluation at 80 significant digits, then their rounded values become
exact rationals for the centered least-squares calculation. This avoids binary64
rank loss when a small voltage span sits on a large offset, but does not make
logarithms exact. Distinct currents merging at the log precision reject the fit;
a zero exact design denominator is also explicitly rejected. Nonzero residuals
can include log-rounding artifacts and are not automatically significant.

Output conversion requires finite binary64 numbers and rejects a nonzero value
that underflows to zero. Derived-range failure leaves the group with no partial
fit or reciprocal. Distinct input values that merge in reported binary64 gate
voltage or current trigger warnings; retained original numeric text and exact
window strings disambiguate selection. Close-to-one R-squared and other rounded
outputs can still hide small differences. Keep original data rather than using
JSON display precision as instrument resolution.

## Interpretation and exit status

The reciprocal may be relevant to a separately justified subthreshold analysis,
but **no output field is a certified subthreshold swing**. Linear-looking log
current can reflect other regimes, leakage, measurement artifacts or an
inadequately small window. Independently establish the appropriate device
physics, bias regime, noise floor, compliance, equilibration and protocol.
Neither a three-point minimum nor high R-squared validates them. The program
does not compare against a thermal bound or infer mechanisms, mobility, trap
density, transistor type or device/ML performance.

- `0`: every group produced a descriptive fit under the documented software
  checks, possibly with warnings or an undefined zero-slope reciprocal.
- `1`: at least one group was rejected; a JSON report retains all rows, successful
  groups, counts and rejection reasons. It is not a malformed-input crash.
- `2`: fatal input, CLI, schema, numeric syntax/resource or output failure.

`inside_window_rows` includes selected rows from rejected groups;
`fitted_rows` includes only rows from successful fits. Do not substitute zero
for null or discard rejected groups without documenting the consequences.

## Background references and verification

These official characterization manuals were checked for definitions, not used
as source code or bundled data:

- Keithley, [Model 4200-SCS Applications Manual, 4200-904-01 Rev. E (June 2008)](https://download.tek.com/manual/4200-904-01E.pdf),
  section 1, pp. 1-2 to 1-4: selected-region line-fit analysis and the distinction
  between slope and its reciprocal.
- Keithley, [ACS-2600 RTM User's Manual, Rev. B (December 2012)](https://download.tek.com/manual/ACS2600RTM-900-01%28B-Dec2012%29%28User%29.pdf),
  section 2, p. 2-18, Figure 34: explicit log10(ID)-versus-VG slope convention.

This tool deliberately uses explicit voltage bounds instead of reproducing the
manuals' current-window interfaces. Its conservative selection, auditing and
numerical rules are software-design choices, not claimed measurement standards.
No vendor code, diagrams or measurement data are redistributed.

```sh
python3 -m unittest discover -s tools/transfer-log-slope/tests -v
python3 run_tests.py
```

The 50 new tests cover base-10 scaling and units, signs/reciprocals, separate
identities and reverse order, selection/audit, invalid rows and whole-branch
conditions, constant/nonmonotonic/curved responses, extreme and near-identical
numbers, parser/resource limits and no-clobber/privacy behavior. The generator
reproduces the fixture bytes. Tested locally on Python 3.12.14 on Linux; other
versions/platforms remain unverified. Passing synthetic tests is not validation
of a real measurement, a subthreshold regime or a trained ML model.
