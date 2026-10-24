# Rust CLI reference adaptation

> Windows permissions inherit directory ACLs; POSIX mode 0600 claims are Unix-only. Windows runtime testing is not claimed.
>
> Native usage is `smlt rram-window`. Any Python API, unittest, or generator below is explicitly reference-only validation under `reference/python/` in the standalone source archive (from a Git checkout, replace that prefix with `../`); it is not called by the Rust executable and is not required to use it. For native tests use `cargo test --locked`; for ready-made native examples use `scripts/run-demo.sh` or `scripts/run-demo.ps1`.

Native command: `smlt rram-window`. Source schema and scientific limitations below are preserved from the Python reference; Python API examples are legacy reference interfaces, not Rust APIs. All bundled data are synthetic software fixtures.

# RRAM resistance-window evaluator

A small, original, Python 3.10+ standard-library CLI for semiconductor
measurement preprocessing. It pairs explicitly labelled HRS/LRS resistance
observations within each device/cycle, checks declared read conditions, and
reports the dimensionless resistance ratio `R_HRS / R_LRS` only for usable pairs.
It does not acquire measurements, access external data, contact APIs, or train a
model. The included data are entirely synthetic.

## Run

From the repository root:

```sh
smlt rram-window --help

smlt rram-window \
  examples/rram-window/synthetic.csv \
  --voltage-unit V --resistance-unit ohm \
  --hrs-label HRS --lrs-label LRS --stdout

smlt rram-window \
  examples/rram-window/synthetic.csv \
  --voltage-unit V --resistance-unit ohm \
  --hrs-label HRS --lrs-label LRS --output new-report.json

python3 -m unittest discover -s reference/python/tools/rram-window/tests -v
```

Choose exactly one of `--stdout` or `--output PATH`; there is no default data
output. Reports contain device/cycle IDs, declared conditions, measurements, and
the input path, so select their destination deliberately. `--output` creates a
new file exclusively, with POSIX permissions `0600` (subject to further umask
restriction). It refuses existing files, input-file replacement, and even
dangling symlinks. It does not create parent directories. It produces no success
message or data on stdout when writing a file. Error messages do not repeat
input paths, identifiers, or raw values. Windows access control is determined
by the operating system; POSIX permission guarantees do not imply Windows ACLs.

Exit codes:

- `0`: a report was produced and every device/cycle group was usable.
- `1`: a report was produced with at least one excluded group. Read its reasons.
- `2`: fatal CLI, CSV/schema, encoding, input, or output error. No diagnostic
  report is produced for malformed input. Output write failures are reported
  and may leave a partial newly created file, which must be discarded. The tool
  does not unlink a pathname after failure, because another process could have
  replaced it.

The synthetic fixture intentionally exits `1`: it contains 13 rows across
2 devices and 7 device/cycle groups; 4 valid pairs, 1 missing-LRS group,
1 voltage-mismatched pair, and 1 pair with an invalid resistance. One valid
ratio is below 1 and is preserved, not relabelled. No production data are used.

## CSV schema

UTF-8 text, with an optional UTF-8 BOM, a comma delimiter, one header, and at
least one observation. Exactly these six case-sensitive column names are
required; their order may change. Additional columns are rejected, not ignored.

```csv
device,cycle,state,read_condition,read_voltage,resistance
sample_A,cycle_001,HRS,protocol_A,0.1,100000
sample_A,cycle_001,LRS,protocol_A,0.1,1000
```

- `device`: nonempty user-assigned device identifier.
- `cycle`: nonempty opaque pairing identifier within the device. `01` and `1`
  are distinct. No integer interpretation, time ordering, consecutive-cycle
  assumption, or cycle completeness assumption is made.
- `state`: exactly one of the required `--hrs-label` or `--lrs-label` values.
  These labels must be distinct, nonempty, and case-sensitive. The CLI does
  not guess them from resistance, names, or row order.
- `read_condition`: nonempty user-declared protocol/condition ID. HRS and LRS
  must have exactly the same ID. Define your IDs so that relevant temperature,
  read timing, pulse/protocol details, instrument configuration, and other
  conditions are not silently mixed. This tool checks declarations only; it
  cannot verify that the actual measurements used matching conditions.
- `read_voltage`: signed decimal/scientific numeric value in the required
  globally declared voltage unit.
- `resistance`: decimal/scientific numeric value in the required globally
  declared resistance unit. Usable measurements must be finite and positive.

Identifiers, condition IDs, state labels, and headers are exact: surrounding
whitespace is rejected rather than silently stripped. Identifiers must contain
no ASCII control characters. Only numeric cells permit surrounding whitespace.
CSV quoting is supported, including identifiers containing commas. NUL bytes,
empty/duplicate headers, incorrect field counts, malformed quoting, blank
records, and empty identifiers are fatal errors. Blank/header-only files are
fatal. Each `(device, cycle, state)` key must occur exactly once: duplicate
observations are rejected even when their numeric fields are invalid. There is
no averaging or silent duplicate selection. Unknown state labels are fatal.

Units are mandatory CLI options, case-sensitive, and apply to every row:

- `--voltage-unit`: `V`, `mV`, or `uV` (ASCII `u`, not the micro symbol).
- `--resistance-unit`: `ohm`, `kohm`, or `Mohm` (`M` denotes mega).

One file must not mix units. Output numeric measurements are normalized to V
and ohm; ratios are dimensionless. Numeric grammar accepts signs, decimal
points, and `e`/`E` notation. It does not accept unit suffixes, thousands
separators, underscores, hexadecimal notation, non-ASCII digits, NaN, or
infinity. Missing/bad/nonfinite numeric cells are counted and exclude their
group rather than causing a structural CSV error.

## Pair rules and numerical behavior

A usable pair requires both user-labelled states, finite positive resistances,
finite signed **nonzero** read voltages, identical exact decimal read voltages,
and identical declared `read_condition` IDs. Positive and negative polarities
are different: `0.1` and `-0.1` do not match. Two negative voltages can match.
`0.1`, `0.10`, and `1e-1` are equal. No tolerance, rounding into equality, absolute
voltage comparison, interpolation, or inferred read bias is used.

Common-unit decimal equality is equivalent to equality under the same exact SI
scaling. Power-of-ten normalization uses Decimal tuple exponents before float
conversion, so values are not rounded by a Decimal arithmetic context. Values
that cannot be represented as finite nonzero Python binary64 floats after SI
normalization are excluded. Zero voltage is separately diagnosed. Ratios use
the normalized binary64 resistances and must also be finite and positive;
overflow or underflow-to-zero is diagnosed as `ratio_out_of_range`. Output JSON
never contains NaN or infinity. Tiny representable/subnormal values remain
subject to ordinary binary64 rounding; this is not a precision or uncertainty
analysis. Instrument limits and meaningful numerical ranges remain the user's
responsibility.

`R_HRS / R_LRS < 1` is retained and explicitly flagged. It is not evidence that
the labels should be swapped; the tool never swaps them. A common-voltage field
may be populated in an excluded pair if voltage matches but another check
fails. Always use `status`, not the presence of that field, to select usable
pairs.

## JSON report contract, version 1.0

Top-level fields:

- `schema_version`, `tool`: version and tool identity.
- `input`: source path, explicit input units, and explicit HRS/LRS label mapping.
- `method`: ratio definition, normalization units, equality rules, and limits.
- `counts`: dataset-level diagnostic counts, including `devices`.
- `per_device`: device-level counts and descriptive ratio summaries.
- `pairs`: one record per observed `(device, cycle)` group, sorted
  lexicographically by those exact string IDs, not chronologically.

Each pair includes `device`, `cycle`, `status` (`valid` or `excluded`), a list of
`reasons`, `hrs`/`lrs` observations (or null for a missing state),
`common_read_voltage_V`, `resistance_ratio`, and `ratio_below_one`. Each observed
state includes `source_line` (ending physical CSV line), `read_condition`,
normalized `resistance_ohm` and `read_voltage_V`, an exact decimal
`read_voltage_input` string for the input unit, and numeric-validation `issues`.
Invalid numeric measurements and unavailable results are JSON null, not zero.

Every `per_device` record contains:

- `counts`, using the same count definitions below except `devices`.
- `by_read_condition_and_voltage`: separate descriptive summaries for valid
  cycles sharing one exact condition ID and one exact signed read voltage.
  Each stratum reports its condition, exact input-voltage decimal string,
  normalized voltage, and ratio summary.
- `mixed_read_conditions_or_voltages`: true if more than one valid stratum
  exists. When true, `resistance_ratio_summary` is null: the tool refuses an
  overall cross-condition ratio summary.
- Otherwise, `resistance_ratio_summary`: `count`, `minimum`, `median`,
  `maximum`, and `arithmetic_mean` of usable paired ratios. For no usable pairs,
  count is zero and the four numeric statistics are null. Even-count medians
  use the midpoint of the two central values. Means and medians are evaluated
  with overflow-avoiding positive-value arithmetic.

Count definitions:

- `rows`: observations, not independent samples.
- `device_cycle_groups`: distinct observed device/cycle keys.
- `complete_pairs`: groups containing both states, including unusable pairs.
- `missing_pair_groups`, `missing_hrs_groups`, `missing_lrs_groups`: observed
  groups missing a state. Entirely absent cycles/devices cannot be counted.
- `invalid_resistance_rows`, `invalid_read_voltage_rows`: row-level invalid
  numeric values, including values outside representable normalized ranges.
  Zero read voltage is representable and instead appears in the pair counter.
- `zero_read_voltage_pairs`: complete pairs with either read voltage zero.
- `voltage_mismatch_pairs`: complete pairs with two numerically valid but
  unequal exact signed read voltages. Invalid-voltage comparisons are not
  counted as mismatches. Zero-versus-nonzero can count as both zero and mismatch.
- `read_condition_mismatch_pairs`: complete pairs with unequal condition IDs.
- `unrepresentable_ratio_pairs`: otherwise eligible pairs whose ratio overflows
  or underflows to zero.
- `valid_pairs`, `excluded_groups`: usable and excluded observed groups.
- `ratios_below_one_pairs`: valid pairs with a ratio strictly below one.

Diagnostics intentionally overlap. One excluded group can have multiple
reasons; do not sum error categories to obtain `excluded_groups`.

## Scientific and ML limits

Cycles are repeated measurements within devices, not independent replicates.
The summaries are descriptive only; there are no hypothesis tests, confidence
intervals, across-device pooled ratio statistics, or effective-sample-size
claims. Measurements from the same device can be correlated. Any downstream
train/test design must consider device identity, conditions, repeated cycles,
and leakage appropriate to the intended generalization question.

The tool does not infer SET/RESET events, state-transition direction, a switching
mechanism, endurance, retention, lifetime, causal effects, pass/fail thresholds,
measurement validity, or model validation. It does not verify calibration,
compliance, read disturbance, pulse history, temperature, timing, drift,
uncertainty, censoring, instrument saturation, or whether a state label is
physically justified. Matching declared metadata is not physical validation.
No scientific claim or performance benchmark is supported by the synthetic
fixture. Keep authoritative raw measurements and their full metadata separately;
this schema is deliberately narrow and does not replace a lab record.

## Python API

`evaluate_csv(path, voltage_unit=..., resistance_unit=..., hrs_label=...,
lrs_label=...)` returns the report dictionary. `evaluate_stream(stream, ...,
source="<stream>")` accepts a text stream with the same schema. Fatal input
problems raise `InputError`; structurally valid data return exclusion diagnostics
instead. The API does not write reports. Use the CLI for the explicit output
destination, redacted-error, and exit-code behavior.

All implementation and tests are original; no external source code, datasets,
models, API credentials, or paid services are required.
