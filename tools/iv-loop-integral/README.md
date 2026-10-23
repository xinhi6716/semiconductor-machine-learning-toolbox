# Ordered I–V Signed-Path Integral

Compute a descriptive **signed trapezoidal integral of the supplied I–V path**
for each explicitly declared device/condition/cycle/replicate loop. Retain every
sample and audit user-declared edge branches. Voltage endpoints must match
exactly; current endpoint equality is reported separately.

The result is in **A·V**, not energy, physical power, geometric lobe area,
intrinsic hysteresis, a switching-state classification or a causal conclusion.
No samples, closure edges, cycles or physical branches are inferred. Python
3.10+ source, standard library only; no network, installation, model, fit,
instrument access or downloaded measurement data.

## Run

From the repository root:

```sh
python3 tools/iv-loop-integral/iv_loop_integral.py \
  tools/iv-loop-integral/examples/01_triangle.csv \
  --voltage-unit V --current-unit uA \
  --accept-supplied-path-assumptions --stdout
```

This original invented fixture has five observations, four edges and two
explicit branches. Contributions are 1.5, 3.5, −2.5 and −0.5 uA·V; the signed
result is **2e−6 A·V**, with exit `0`. The turnaround sample is referenced by
both branches but remains one observation. This is arithmetic, not a device
simulation or experimental validation.

Replace `--stdout` with `--output NEW_REPORT.json` to create a new private file.
Exactly one destination is required. The acknowledgment flag accepts the
supplied-order, signed, piecewise-straight path convention and its limitations;
it does not certify chronology, actual physical closure or measurement quality.
There is no default unit, branch filter, open-loop mode, closure tolerance,
normalization, sign correction, automatic cycle/branch inference or resampling.

## Input contract

UTF-8 comma-separated CSV with exactly these case-sensitive unique columns,
in any header order:

```csv
lot,wafer,device,condition,cycle,replicate,sample_id,branch_to_next,voltage,current
invented_lot,invented_wafer,invented_device,invented_protocol,001,repeat_1,p001,up,0,0
invented_lot,invented_wafer,invented_device,invented_protocol,001,repeat_1,p002,down,1,3
invented_lot,invented_wafer,invented_device,invented_protocol,001,repeat_1,p003,,0,1
```

- `(lot,wafer,device,condition,cycle,replicate)` is one exact declared loop key K.
  Each K occupies one contiguous block. Reappearing after another K is fatal,
  not silently rejoined. No edge crosses K. Groups and all samples retain
  supplied order; nothing is sorted. Cycle and replicate strings are not times
  or integers; `001` and `1` remain distinct.
- Physical-device identity is `(lot,wafer,device)`, not the loop key. Repeats,
  labels and branch references do not establish statistical independence. The
  tool reports no count of independently sampled physical devices.
- `condition` links to separately retained protocol records: terminal/sign
  convention, temperature/environment, state preparation, waveform/range/rate,
  sampling/settling/dwell/pulse timing, compliance/clipping, instrument,
  calibration and any prior correction. A fixed protocol can intentionally
  specify different positive/negative compliance. The software checks labels,
  not actual conditions; it never equates different condition IDs.
- `sample_id` identifies one observation within K and must be unique there.
  It may recur in another K without establishing independence. Equal numeric
  samples with distinct IDs remain distinct. The final numerical return needs
  its own observation ID; the tool cannot detect an exporter fabricating it.
- Nonempty IDs are 1–128 Unicode characters, unpadded and without ASCII controls
  (0–31 or 127). Case/codepoints are preserved, not normalized. Quoted commas
  are supported. JSON-only output does not apply spreadsheet-formula filtering;
  a later CSV exporter needs its own safeguards.

### Outgoing-edge branch ownership

`branch_to_next` on sample i labels **edge i, from sample i to sample i+1**
in the same K. Every nonfinal row needs a nonempty valid label, including
flat/vertical edges. The final row must have an empty field because it owns no
edge. A missing nonfinal label or extra final label is a fatal structural error.

Each label must occupy one contiguous edge block. Reusing it after another label
rejects the loop; blocks are never silently merged. Positive and negative voltage
steps in the same block reject the loop. Flat edges can belong to a directional
block or an explicitly labeled hold block. Adjacent labels with the same direction
stay separate. Nonreversing branches are a conservative software contract, not a
mathematical necessity of the integral or proof of a stable physical state.

For `(V,I,branch_to_next)`:

```text
(0,0,up), (1,2,hold), (1,3,hold), (1,4,down), (0,0,empty)
```

Up owns edge 1; hold owns edges 2–3; down owns edge 4. Samples 2 and 4 are shared
endpoints, stored once. The three equal-voltage samples are distinct retained
observations. There is no omitted transition edge or fabricated bridge.

The existing [sweep-segment audit](../sweep-segments/README.md) can inspect
coordinate-direction changes. This tool instead audits explicit edge labels;
neither automatically exports scientifically valid branches into the other.

### Units, syntax and resource limits

Required units apply to the entire corresponding column:

- Voltage: `V`, `mV`, `uV`, `nV`.
- Current: `A`, `mA`, `uA`, `nA`, `pA`.

SI factors are exact powers of ten. Plausible but mislabeled units cannot be
detected. Current is signed terminal current, not current density. No absolute
value, leakage subtraction, offset correction, polarity inference or clipping.

A numeric token is unpadded signed decimal/scientific text, at most 64 characters,
32 parsed Decimal coefficient digits, and absolute **stored Decimal exponent**
at most 200. Trailing coefficient zeros count: `1.0e-200` has stored exponent
−201 and fails. Exponent suffixes contain 1–4 digits. Zero, including signed zero,
is permitted. Every nonzero exact SI input magnitude must be in [1e−150,1e150].
These are computational bounds, not instrument specifications. Unit suffixes,
separators, underscores, hexadecimal, NaN, infinity or missing numbers are fatal.
All rows are checked, even those in a loop that will be rejected.

Optional UTF-8 BOM and LF/CRLF are accepted. Blank records, wrong row widths,
missing/duplicate/extra headers, malformed encoding/CSV, empty/header-only files
are fatal. A final line ending is allowed; an additional empty record is not.
No row is silently skipped. Only regular input files are read, with a nonblocking
open and file-type check. An input symlink may resolve to a regular file.

Hard limits: 8 MiB input, 10,000 data rows, 1,000 loop keys, 1,000 rows per loop;
4,096-bit exact rational numerator/denominator; 1,024-character exact output
numbers; and 64 MiB fully serialized UTF-8 JSON. Budget failure is fatal rather
than a partial audit. No input is modified or transmitted.

## Defined calculation and closure

For retained SI samples `(V_i,I_i)`, i=1,...,n, in supplied order:

```text
delta_V_i = V_(i+1) - V_i
T_i = ((I_i + I_(i+1)) / 2) * delta_V_i
signed_path_integral_A_V = sum(T_i), i=1,...,n-1
```

This is exact for the defined piecewise-straight path. For an unknown physical
trajectory between observations it is a trapezoidal descriptor without a
physical quadrature-error bound. No intermediate samples or crossings are made.
Flat-voltage edges contribute zero but retain their current changes and IDs.
Dwell intervals can consume physical energy while contributing zero here.

A usable result requires at least three samples, nonzero voltage span, exact
`V_last == V_first`, valid declared branches and finite numeric summaries.
There is **no tolerance, endpoint snapping or automatic closure**. Equivalent
exact decimals compare equal; unequal values that alias in binary64 still fail.

The closure fields explicitly use **last minus first**, in SI:

- `voltage_endpoint_difference_V_exact = V_last - V_first`, volts.
- `current_endpoint_difference_A_exact = I_last - I_first`, amperes.
- `iv_endpoint_closed_exact` requires equality of both coordinates.

Voltage closure with unequal current endpoints is allowed as a **supplied-path**
descriptor, with a warning that the observed I–V polygon is not closed. No edge
is added. A hypothetical vertical return at the common voltage would contribute
zero to I dV, but it is not a measured transition and may be physically unjustified.
It is not counted or used to claim an observed enclosed area.

### What the result does not mean

Electrical terminal energy is `integral V(t)*I(t) dt`, in J, with justified time
alignment and terminal convention. This tool has no time channel. A·V has the
same SI dimension as W, but this path integral is neither energy nor electrical
power. Dividing it by a sweep rate does not fix that: the energy expression also
needs the factor V.

For a genuinely closed simple polygon, V horizontal and I vertical, this signed
integral is the negative of counterclockwise signed area: clockwise is positive.
For crossing/retraced paths, cancellation and traversal multiplicity remain.
Neither `abs(total)` nor `sum(abs(edge contributions))` is generally geometric
lobe area. The tool does not find intersections or compute unsigned lobe areas.
Fixture 06 has zero signed total but two nonzero lobes; zero is not evidence of
absent hysteresis. Nonzero values do not establish intrinsic hysteresis either.

Reversing traversal negates the result; edge labels must be remapped to reversed
edges. Negating I or V alone negates it; negating both preserves it. Adding constant
current c changes the total by `c*(V_last-V_first)`. Only exact voltage closure
guarantees offset invariance of the **total**, not individual branches,
branch-specific offsets, drift immunity or correct calibration. Uniform voltage
translation leaves voltage differences unchanged. Exactly collinear supplied
subdivision preserves the integral, but the tool never inserts that sample.

## Exact arithmetic, JSON audit and rejections

Decimal parsing, exact SI conversion, comparisons, products and sums use rational
arithmetic, never binary64 accumulation or a global Decimal rounding context.
All exact results terminate in decimal. Original numeric text is preserved;
canonical SI and edge values are strings such as `"2e-6"`, `"-1.25e+0"`, or
`"0"`. Nonzero strings have one leading nonzero digit, no trailing fractional
zeros, and an explicitly signed exponent without leading zeros; exponent zero
is `+0`, never `-0`. No trailing newline/character is allowed.

Usable loop and branch summaries provide both exact strings and finite binary64
numbers. Exact strings are authoritative. Finite/subnormal numbers may round or
alias distinct exact values; they never determine equality or validity. A
nonzero exact summary converting to zero, or any nonfinite conversion, rejects
the **whole loop**, including when only a branch underflows. Nothing is clipped
or replaced with zero. Exact zero is numerical `0.0`, not negative zero.

All applicable semantic reasons are reported in this order:

1. `insufficient_samples`.
2. `zero_voltage_span`.
3. `voltage_endpoints_not_equal`.
4. `noncontiguous_declared_branch`.
5. `reversing_declared_branch`.
6. `derived_numeric_range_error`, checked only if reasons 1–5 are absent.

`primary_rejection_reason` is the first reason; `status=usable` means only that
these software checks passed. Rejected groups have `metrics=null` and every
branch's binary64 summary null. All observations and exact edge/branch audits
remain. No overall exact candidate sum is exported for a rejected open path.
Never zero-fill a rejected group or silently treat an exit-1 cohort as complete.

[report_schema.json](report_schema.json) defines structural schema version 1.
Additional normative semantic invariants are:

- K, observations and edges preserve supplied order. Every source record occurs
  once; n samples give exactly n−1 edges. Every edge belongs to one declared
  block. Block ranges partition the edge indices with no omission/duplication.
- A block of edges a,...,b references samples a,...,b+1. Neighboring blocks share
  exactly the same endpoint observation. Each sample's membership list and each
  shared-endpoint reference agree; references are not extra observations.
- Exact edge values satisfy the formula, and usable exact branch totals sum to
  the exact loop total. Float sums may differ through rounding.
- Input IDs are unpadded, including Unicode boundary whitespace; this is a
  semantic parser check in addition to the schema's strict control-free pattern.
  All array counts, cross-field relations and exact canonical values are checked
  by their stated semantics, not by JSON Schema alone.

The report contains parameters, limits, counts, every K, all original numeric
text, exact SI observations, signed edge deltas/contributions, declared branch
blocks, shared identities, exact closure differences, warnings and feature timing
limits. Indices are one-based: source record excludes the header; physical line
start/end includes the header; sample/edge/block indices are local to K.
`input_sha256` and byte count describe the same bounded CSV bytes actually parsed,
including BOM/line endings, not an unprovided instrument export. Keep that original
export plus a documented sample-ID adapter mapping. Hashes do not recover data.

## Output safety, API and exit status

`--stdout` deliberately exposes raw IDs, values and audit. `--output` creates a
new file exclusively, requesting POSIX mode `0600`; existing files, directories,
input paths and live/dangling symlinks are never overwritten. The parent directory
must exist. Other platforms may have different permission semantics. Serialize
and validate limits before opening a destination. Errors redact IDs, values and
paths. An I/O failure can leave an incomplete new file: discard it after exit 2.

- Exit `0`: complete report, all groups software-usable.
- Exit `1`: complete audit report, at least one rejected group retained.
- Exit `2`: fatal CLI/input/resource/serialization/I/O error; no valid report promised.

The callable is:

```python
evaluate(path, *, voltage_unit, current_unit,
         accept_supplied_path_assumptions=False) -> report_dict
```

It reads/hashes one bounded snapshot, has no write/network side effects, and
requires explicit supported units and acknowledgment `True`. Fatal input/argument
errors raise redacted `InputError`; per-loop semantic failures appear in the
returned report. Its serialized report size is also bounded. No target, model or
automatic branch-export API is provided.

## Feature timing and independent units

The complete loop **and every used annotation** must exist before the feature
is available. `feature_availability` references the final retained sample and
always states `time_verified=false`, `prediction_eligibility=not_established`.
Cycle/replicate strings and row order are not timestamps.

Before predicting a future outcome, separately verify original timestamp/event
records show the full feature was available at or before the prediction origin
and the target occurs strictly later. Unknown, equal or overlapping timing does
not validate future prediction. A completed-loop feature cannot prospectively
predict an event during that loop. Future cycles, target-derived labels, branch
boundaries and post-outcome protocol choices can leak information. Split at the
experiment's true dependency level (device/wafer/lot/time/campaign) before learned
preprocessing. Branches and repeated cycles are not independent devices. This
tool does not validate temporal suitability or make predictive/causal claims.

## Verification and original examples

```sh
python3 -m unittest discover -s tools/iv-loop-integral/tests -v
python3 tools/iv-loop-integral/examples/generate_synthetic.py 01_triangle
python3 run_tests.py
```

Twenty original invented CSV fixtures are regenerated exactly by the generator.
`examples/expected.json` records exact arithmetic and intended exits. Cases 01–06,
12,16,17 exit 0; 07–11 and 13–15 exit 1; 18–20 are deliberate structural errors
and exit 2 without a report. Use V/uA except 13–16, which require V/A. Oracle-only
sums in the expected fixture file are test evidence, not exported rejected metrics.

Cases include traversal/sign/offset invariance, unequal current endpoints,
plateaus, self-crossing cancellation, open loops, exact endpoint float aliases,
invalid labels, zero currents, large cancellation, and legal input whose total
or branch underflows binary64. Tests cover strict schemas, units, complete
lineage, independence of Decimal context, numeric/resource limits, all exit codes,
regular-file input, exclusive private output, redacted errors and partial writes.
Tests establish software behavior on the executed runtimes, not measurement
accuracy, physical validity or real-device performance. See the root README for
actual runtime verification; no hosted CI is configured.

## Definition and measurement-context sources

- [NIST SP 330, Section 2](https://www.nist.gov/pml/special-publication-330/sp-330-section-2):
  SI relationships W=J/s and V=W/A. Dimensional equivalence does not establish
  that this descriptor measures electrical power.
- [MIT 22.105, Chapter 3, equations 3.7–3.10](https://ocw.mit.edu/courses/22-105-electromagnetic-interactions-fall-2005/c2f968d15662cff0b755fd027d18582e_chap3.pdf):
  current as charge/time and electrical work as a time integral of IV.
- [MIT 18.02SC, Green's theorem and area](https://ocw.mit.edu/courses/18-02sc-multivariable-calculus-fall-2010/pages/3.-double-integrals-and-line-integrals-in-the-plane/part-c-greens-theorem/session-68-planimeter-greens-theorem-and-area/):
  the closed-path/area relation; the signed convention and cancellation
  counterexamples here are stated explicitly.
- [SciPy's official trapezoid definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.trapezoid.html):
  ordered composite trapezoids retain x order. No SciPy installation, import or
  implementation is used by this tool.
- [Xie, Afshari and Sanchez Esqueda, npj 2D Materials and Applications 6, 50 (2022)](https://www.nature.com/articles/s41699-022-00328-2):
  original RRAM-context measurements illustrate repeated cycles, signed sweeps,
  polarity-dependent compliance and separate pulse-duration energy accounting.
  No data, figures or device-model values from the paper are redistributed.
