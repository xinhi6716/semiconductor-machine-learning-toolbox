# I–V Readpoint Extractor

Extract one **signed current** and **static conductance** feature at an explicitly
specified read voltage, separately for every declared device/cycle/branch.
Designed for semiconductor electrical measurements, including hysteretic
resistive-switching sweeps. It does not fit a device model or infer switching
states.

## Run

Python 3.10+; standard library only. From the repository root:

```sh
python3 tools/iv-readpoint/iv_readpoint.py \
  tools/iv-readpoint/examples/synthetic_iv.csv \
  --voltage-unit V --current-unit uA \
  --read-voltage 100 --read-voltage-unit mV --stdout
```

The invented fixture returns four separate branch readpoints. For example,
`synthetic_A`, cycle `1`, branch `post_program_up` gives approximately
`current_A = 0.00001` and `conductance_S = 0.0001`. These numbers verify software
arithmetic only; they do not describe an experimentally validated device.

To create a private new report instead of printing raw IDs and measurements:

```sh
python3 tools/iv-readpoint/iv_readpoint.py \
  tools/iv-readpoint/examples/synthetic_iv.csv \
  --voltage-unit V --current-unit uA \
  --read-voltage 100 --read-voltage-unit mV \
  --branch post_program_up --max-bracket-span 110 \
  --output iv-readpoints.json
```

`--max-bracket-span` is in the **read-voltage unit**, not necessarily the input
voltage unit. It bounds the gap used for interpolation; it is not an accuracy or
physical-validity guarantee. An exact sample needs no interpolation and is not
excluded by this gap limit.

## CSV contract

Exactly five columns, in any order, with exact unpadded names:

```csv
device,cycle,branch,voltage,current
synthetic_A,1,post_program_up,0.05,5
synthetic_A,1,post_program_up,0.15,15
```

- `device`, `cycle`, and `branch` are nonempty opaque strings, not numeric labels.
  Leading/trailing whitespace, control characters, and identifiers longer than
  256 characters are rejected. `001` and `1` are distinct cycle IDs.
- Each branch label must identify one acquisition-ordered, monotonic-voltage
  segment in a fixed state/protocol. Put state/phase in the branch label when
  needed. The tool does not know that a name denotes HRS, LRS, SET, or RESET.
- Preserve acquisition order within each device/cycle/branch. Interleaving groups
  is allowed; their relative row order is preserved. Do not concatenate repeated
  sweeps under the same key. Use distinct cycle or branch IDs.
- Voltage units: `V`, `mV`, `uV`. Current units: `A`, `mA`, `uA`, `nA`, `pA`.
  Units are mandatory CLI declarations applying to the whole file. Mixing units
  within a column is not supported. Read-voltage units are independently explicit.
- UTF-8 and an optional UTF-8 BOM are accepted. Completely blank physical lines
  are ignored. Whitespace-only lines and delimiter-only rows are errors. Extra,
  missing, or duplicate columns, malformed CSV, and nonnumeric/nonfinite fields
  are fatal errors. Scientific decimal notation is accepted; unit suffixes and
  numeric underscores are not. Numeric text is limited to 128 characters.
- Unknown columns are rejected, not silently dropped. If your export contains
  temperature, delay, compliance, or pulse settings, first partition it into
  correctly identified fixed-condition branches and preserve its metadata in
  your measurement record. The tool cannot verify those physical conditions.

## Algorithm and rejection rules

1. Normalize explicitly declared units to SI. Retain signed voltage and current.
2. Group by `(device, cycle, branch)` without reordering measurements.
3. Reject any duplicate voltage, whether currents agree or conflict. Values that
   become identical after binary64 SI conversion count as duplicates. Reject
   voltage reversals instead of sorting them away.
4. Use an exact matching read-voltage sample when present. A one-point branch is
   usable only when its voltage matches the target.
5. Otherwise find the two **adjacent** bracketing voltages in that same validated
   branch and use `I = I_low + (V_read - V_low) / (V_high - V_low) * (I_high - I_low)`.
   Never extrapolate or interpolate across branches, cycles, or devices.
6. Calculate `G_static = I / V_read`. A zero read voltage is a fatal error. A zero
   current is valid and gives zero conductance. Negative signed conductance is
   retained, not converted to an absolute value or interpreted physically.

Normalized inputs and JSON outputs use finite binary64 numbers. Intermediate
interpolation arithmetic uses exact rational representations of those normalized
numbers to avoid preventable subtraction overflow. Nonzero underflow to zero and
nonfinite derived values reject the group. There is no uncertainty propagation.
Measured-point equality and duplicate checks are exact **after SI binary64
conversion**; there is no hidden voltage tolerance or rounding policy.

## Report and exit status

JSON contains parameters and units, assumptions, group counts, rejection counts
by reason, and one result per selected branch. Usable results include:

- `device`, `cycle`, `branch`, `sample_count`, `acquisition_direction`
- `read_voltage_V`, `current_A`, `conductance_S`
- `method`: `measured` or `linear_interpolation`
- `source_rows`: 1-based physical CSV line numbers of the used samples
- `bracket_voltage_V`, `bracket_current_A`, `bracket_span_V`

Rejected results name one deterministic reason, such as `duplicate_voltage`,
`nonmonotonic_voltage`, `read_voltage_outside_branch`,
`bracket_span_exceeds_limit`, or `derived_numeric_range_error`. Filtering a branch
changes which groups are evaluated, but the entire CSV must still be parseable.

- Exit `0`: report produced, all selected groups usable.
- Exit `1`: report produced, at least one selected group rejected. Usable groups
  remain in the report; inspect counts before using them as ML features.
- Exit `2`: CLI, input/schema, or output error. No valid report is promised.

Explicit `--stdout` or `--output PATH` is required. Errors do not echo raw values,
identifiers, or paths. A file is created exclusively with requested POSIX mode
`0600` and is never overwritten, including when its path is an existing symlink.
Parent directories must exist. An OS write failure may leave a partial newly
created file; discard that file after a nonzero fatal exit. Do not commit reports
containing private measurements.

## Physical interpretation and ML use

The readpoint is an observed/interpolated terminal feature under the declared
measurement protocol. `I/V` is **static conductance**, not differential `dI/dV`,
not a material conductivity, and not a claim that the device is Ohmic. Linear
interpolation may be inappropriate near an abrupt transition, compliance limit,
read-disturb regime, or noise floor. Pick a suitable read voltage and gap bound
using experimental knowledge; this utility does not decide them.

For ML, keep the device and cycle/branch provenance with every feature. Split
training/evaluation by the independent physical unit relevant to your experiment
(for example device, wafer, or fabrication lot), not by randomly scattering
repeated measurements from the same device across both sets. The program does
not create splits, train models, or establish an independent sample size.

No SET/RESET threshold, HRS/LRS state, microscopic mechanism, lifetime, endurance,
retention, or device-performance claim is inferred.

Background for measurement context, not source code or validation evidence:
[Keysight: I–V output measurements](https://helpfiles.keysight.com/Standalone_BenchVueSoftware_PW_HelpFiles/PWPowerAppSuite/Content/IV%20Curve%20Measurement/Setup%20Source%20and%20Sampling/Setup%20Source%20-%20Output%20Signal.htm)
and [Lanza et al., Recommended Methods to Study Resistive Switching Devices](https://poplab.stanford.edu/pdfs/Lanza-RecommendedMethodsRRAM-aem19.pdf).
The implementation and all fixtures are original; no article data are included.

## Tests

```sh
python3 -m unittest discover -s tools/iv-readpoint/tests -v
```

Tests cover ascending/descending and interleaved branches, exact/linear reads,
units/polarity, branch isolation, malformed inputs, duplicates/reversals,
extrapolation prohibition, extreme finite arithmetic, deliberate outputs,
permissions, symlinks, and exit codes. These are software tests, not hardware or
scientific validation.
