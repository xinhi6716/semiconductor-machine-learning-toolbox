# I–V Voltage-Grid Audit

Before comparing or fitting semiconductor I–V sweeps, check whether the declared
sweeps actually contain the same voltage coordinates. This tool reports exact
repeated voltage coordinates, distinct grid variants and coordinates absent from
one sweep but present in another. It retains every supplied observation.

This is **not** a response-value audit, a fit, a branch classifier or a certificate
of usable training data. Only required identities and `voltage` are decoded.
Additional columns, including `current`, `target` and other measurement channels,
are opaque. Their contents are not converted, checked for finiteness or emitted.

## Quick start

Python 3.10+, standard library only. Run from the repository root:

```sh
python3 tools/iv-grid-audit/iv_grid_audit.py \
  tools/iv-grid-audit/examples/synthetic_equal.csv \
  --voltage-unit V --stdout

python3 tools/iv-grid-audit/iv_grid_audit.py \
  tools/iv-grid-audit/examples/synthetic_findings.csv \
  --voltage-unit V --stdout
```

Both files are original invented software fixtures, not laboratory data or device
simulations. Each has six observations in two declared sweeps of one device label:

- `synthetic_equal.csv` exits `0`: both grids are `{0, 0.5, 1}`. Different row order,
  decimal spellings and signed zero do not change set equality. The second sweep's
  reversed order is intentionally **not** validated by this tool. The opaque
  `current` column includes `NaN`, demonstrating that a clean grid audit says
  nothing about response validity.
- `synthetic_findings.csv` exits `1`: the second sweep contains `0, 1.0, 1e0`.
  Rows 5 and 6 are distinct observations at the same exact voltage. That sweep is
  missing `0.5` relative to the observed union `{0, 0.5, 1}`; neither duplicate is
  removed or averaged.

To save rather than print raw identifiers and voltages, replace `--stdout` with
`--output NEW_PATH.json`. No existing path or symlink is overwritten. New reports
request POSIX mode `0600`. A failed write can leave an incomplete new file; discard
it after exit `2`. No network access, installation, fitting, automatic branch
export, input modification or external service is used.

## Input contract

A comma-separated CSV must contain these exact, case-sensitive required columns
in any order:

```csv
lot,wafer,device,condition,sweep,branch,sample_id,voltage
invented_lot,invented_wafer,invented_device,gate_protocol,repeat_1,forward,p1,0
invented_lot,invented_wafer,invented_device,gate_protocol,repeat_1,forward,p2,0.5
```

Additional uniquely named columns are allowed but **their values are opaque**.
All header names use `[A-Za-z][A-Za-z0-9_]{0,63}`. Headers are never guessed or
normalized; a `Voltage` field does not satisfy `voltage`. Do not rename a target
column to `voltage`. This deliberately narrow schema audits voltage only.

Identity rules:

- A declared sweep-group key is the exact tuple
  `(lot, wafer, device, condition, sweep, branch)`, not a concatenated string.
  Different lots/wafers/device labels remain separate. A group may be interleaved
  with other groups; its samples retain source-record order and global ordinals.
- `sample_id` must be unique within that full key. Reuse in another key is allowed.
  Duplicate sample IDs are fatal because observation identity is ambiguous.
  Distinct samples at an equal voltage are retained and reported as a finding.
- IDs are UTF-8 text, 1–128 characters, unpadded and free of Unicode control
  characters (category `Cc`). Case, Unicode spelling, quoted commas and leading
  zeroes are preserved exactly. `001` and `1` remain distinct.
- `sweep` and `branch` are user declarations, not inferred cycles or acquisition
  histories. A sweep group is **not** a count of independent physical DUTs.

Voltage rules:

- `--voltage-unit` is required: `V`, `mV`, `uV` or `nV`. One declared unit applies
  to the entire file. There is no conversion or verification of physical units,
  voltage reference terminal, instrument accuracy or actual protocol.
- Each selected voltage is a finite signed ASCII decimal/scientific number.
  Padded values, blanks, unit suffixes, commas, underscores, hexadecimal tokens,
  NaN and Infinity are fatal, not repaired or excluded.
- The maximum token length is 64 characters, with at most 32 parsed Decimal
  coefficient digits and absolute **stored Decimal exponent** at most 200.
  Trailing coefficient zeros count. For example, `1e-200` is accepted but
  `1.0e-200` has stored exponent `-201` and is rejected. These are resource limits,
  not an instrument range or accuracy claim.
- Equality and ordering use exact Decimal comparisons without float conversion,
  arithmetic, rounding, tolerance or changing the active Decimal context.
  `1`, `1.0` and `1e0` compare equal, as do signed zero variants. Original selected
  field spellings are retained as JSON strings in every sample's `voltage_text`.

CSV framing and bounds:

- A UTF-8 BOM, LF/CRLF record endings and a final newline are accepted. CSV quoting
  and escaped double quotes are supported. Blank records, missing/extra fields,
  malformed quoting, bare CR record separators, empty files and header-only files
  are fatal. Logical data-row ordinals exclude the header; embedded newlines in
  an opaque quoted field do not create an extra record.
- All file bytes are read and hashed. Opaque fields are traversed only to check
  delimiters, quoting, record width and byte limits. Even arbitrary non-UTF-8
  bytes inside an opaque value are not decoded. Invalid UTF-8 in a required ID
  or non-ASCII selected voltage remains fatal. Changing opaque values may change
  the input digest/byte count, but not the audit results if framing and limits
  remain valid. Opaque values are not preserved in the report; retain the input.
- Only regular input files are accepted: maximum 8 MiB, 64 columns, 65,536 bytes
  per logical record including any record ending, 10,000 data records and 1,000
  declared sweep groups. The sum of `observed_union_size × group_count` over
  comparison sets must be at most 100,000, checked before grid-list expansion.
  Complete serialized JSON is limited to 16 MiB. Exceeding any limit is fatal;
  there is no truncated or partially accepted report.

## Comparison rules and limits

Comparison sets use exact **global `(condition, branch)` labels**. All declared
sweep groups with those two labels are compared, including groups on different
lot/wafer/device tuples. Scope condition labels accordingly: use different labels
when fixed biases, temperature, waveform, measurement protocol or intended grid
differ. Matching label text does not prove that these physical conditions match.
Other metadata columns are opaque, so this tool cannot discover a hidden fixed-
bias or protocol change in them. Never infer label equivalence across exports.

For each comparison set:

1. Build the numerically sorted union of exact observed voltage values.
2. Assign one-based coordinate indices local to that comparison set. Each union
   entry retains the earliest source-record voltage spelling, even if groups
   interleave; it is not a repaired or
   averaged measurement. All individual spellings remain in the sample records.
3. Represent each sweep's grid as its sorted **unique** coordinate indices.
   This set view does not drop observations from `samples`.
4. Group identical sets into `grid_variants`; more than one variant is an unequal-
   grid finding. Identical point counts alone do not establish equal grids.
5. List exact repeated coordinates and all of their source-row ordinals within
   each group, plus indices absent from that group but in the observed union.

“Missing” always means **missing relative to the observed union**, not a planned
or physically required voltage schedule. The union is not a ground-truth reference.
The tool cannot detect a coordinate missing from every supplied group, a wholly
absent sweep, mislabeled conditions, undocumented acquisitions or an intended
comparison split into different labels. A singleton comparison set cannot reveal
between-sweep mismatch. Duplicate coordinates may be intentional repetitions.
Neither findings nor their absence select a scientific cohort or authorize
removing/interpolating data.

Grid equality intentionally ignores sample order, direction and multiplicity.
Use the separate [ordered sweep-segment audit](../sweep-segments/README.md) when
coordinate order is the question. Neither audit establishes physical hysteresis,
stable conditions, independent observations, response/target finiteness, statistical
eligibility or fitness for an unspecified model. No target, current, resistance,
feature table, model prediction or scientific conclusion is produced here.

## Report schema and exit status

`schema_version` is `iv-grid-audit-v1`. Output is deterministic JSON with:

- `status`: `NO_GRID_FINDINGS` or `GRID_FINDINGS`.
- `provenance`: SHA-256 of the exact input bytes, byte count and logical-record
  ordinal convention. This identifies the input CSV, not an upstream export.
- `selection`: decoded/opaque column names and `response_values_inspected: false`.
- `parameters`: declared voltage unit, `unit_conversion: "none"`, exact Decimal
  set comparison and comparison-key columns.
- `counts`: input rows, declared sweep groups, comparison sets, groups with
  duplicate coordinates and comparison sets with unequal grids. None is an
  independent-device count.
- `groups`: one-based `group_index`, full `identity`, `comparison_index`, ordered
  `samples` (`source_row_ordinal`, `sample_id`, `voltage_text`, `coordinate_index`),
  sorted `unique_coordinate_indices`, `duplicate_coordinates` (coordinate index
  plus all source-row ordinals), and `missing_from_observed_union_indices`.
- `comparisons`: one-based `comparison_index`, exact `condition` and `branch`,
  group indices, sorted `observed_union` (coordinate index plus
  `first_observed_voltage_text`), `grid_variants` (coordinate indices plus group
  indices) and boolean `unequal_grids`. Coordinate references are local to their
  comparison; group references are global to the report.
- `limitations`: interpretation boundaries, not inferred quality judgments.

Groups/comparisons/variants follow first appearance. Duplicate-coordinate entries
follow first appearance within the group; samples retain supplied order. Numeric
union and unique/missing grid index lists are sorted.

Exit `0` means no exact coordinate duplicates or unequal sets were found among
the supplied groups. Exit `1` means a complete audit contains one or both findings.
Both retain all supplied samples and say nothing about response values or
scientific validity. Exit `2` means malformed selected input/CSV, a resource limit,
CLI misuse or I/O failure; no complete report is claimed. Diagnostics are
categorical and do not echo raw paths, identifiers or data values. `--stdout` is
an explicit opt-in to printing raw identities and voltage coordinates.

## Verification

```sh
python3 -m unittest discover -s tools/iv-grid-audit/tests -v
python3 run_tests.py
```

Tests cover exact grid semantics and row lineage; namespace separation; distinct
samples at equal coordinates; numeric syntax/ranges and binary64 alias avoidance;
opaque-response noninterference (including arbitrary bytes and multiline CSV);
malformed inputs and derived-report bounds; documented examples; exclusive output;
and failed/absent output and diagnostic streams. These are software fixture tests,
not real-device validation. The source targets Python 3.10+; this addition was
executed with Python 3.12.14 on Linux, not every supported interpreter/platform.
