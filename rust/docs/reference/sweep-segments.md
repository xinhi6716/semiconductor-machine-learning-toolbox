# Rust CLI reference adaptation

> Windows permissions inherit directory ACLs; POSIX mode 0600 claims are Unix-only. Windows runtime testing is not claimed.
>
> Native usage is `smlt sweep-segments`. Any Python API, unittest, or generator below is explicitly reference-only validation under `reference/python/` in the standalone source archive (from a Git checkout, replace that prefix with `../`); it is not called by the Rust executable and is not required to use it. For native tests use `cargo test --locked`; for ready-made native examples use `scripts/run-demo.sh` or `scripts/run-demo.ps1`.

Native command: `smlt sweep-segments`. Source schema and scientific limitations below are preserved from the Python reference; Python API examples are legacy reference interfaces, not Rust APIs. All bundled data are synthetic software fixtures.

# Ordered Sweep Segment Audit

Map a semiconductor voltage or magnetic-flux-density sweep into exact increasing,
decreasing and flat **edge runs**, while retaining every supplied observation.
Use this before preparing explicit branches for the I–V readpoint,
constant-current Vth or Hall-response tools, which reject repeated coordinates
and reversals within one declared branch.

This is an audit of **supplied row order**, not a reconstruction of acquisition
chronology or an automatic physical branch classifier. It does not infer cycles,
independent replicates, stable conditions, switching states or physical
hysteresis. It does not export ready-to-fit branches or alter another tool's
numerics. No dependency installation, network access or instrument access is used.

## Quick start

Run from the repository root with Python 3.10+:

```sh
smlt sweep-segments \
  examples/sweep-segments/synthetic_hall_coordinates.csv \
  --axis magnetic-flux-density --coordinate-unit mT --stdout

smlt sweep-segments \
  examples/sweep-segments/synthetic_gate_coordinates.csv \
  --axis voltage --coordinate-unit V --stdout
```

Both fixtures are original invented coordinate sequences, not measurements,
device simulations or extracted research data. Both commands exit `0`:

- Hall-style fixture: an invented two-leg `-70 → +70 → -70 mT` path,
  **281 samples, 280 edges, two segments of 140 edges each**.
  `sample_141` at `70 mT` is the one shared turnaround observation. The two
  segments each reference 141 samples, but their 282 references identify only
  281 observations. That extra reference is not an extra measurement.
- Gate-style fixture: **10 samples, nine edges, six segments, three flat edges**,
  five shared endpoint samples and two coordinate-direction reversals. One
  reversal spans a three-sample plateau; another plateau resumes the same
  direction and is not counted as a reversal.

To save a report instead of printing raw identifiers and values, replace
`--stdout` with `--output NEW_PATH.json`. The destination must not exist.

## Input contract

Use a separate UTF-8, comma-separated, coordinate-only CSV with exactly these
case-sensitive columns; column order may change:

```csv
lot,wafer,device,condition,sequence,sample_id,coordinate
invented_lot,invented_wafer,invented_device,gate_protocol,supplied_A,p001,0
invented_lot,invented_wafer,invented_device,gate_protocol,supplied_A,p002,1
invented_lot,invented_wafer,invented_device,gate_protocol,supplied_A,p003,1.0
invented_lot,invented_wafer,invented_device,gate_protocol,supplied_A,p004,0
```

- `(lot, wafer, device, condition, sequence)` is an exact tuple key, not a joined
  string. Each key must occupy **one contiguous block** of CSV records. A key
  that reappears after another key is a fatal error, not silently rejoined.
  No edge crosses a group boundary. Groups and samples retain supplied order.
- `sequence` identifies a user-declared ordered record. It need not be one cycle,
  one acquisition or an independent replicate; the software establishes none
  of those facts. Do not relabel repeated acquisitions as new physical devices.
- `condition` links to the protocol record; its string does not prove stable or
  known experimental conditions. Preserve your terminal/field-axis convention,
  fixed biases, temperature, timing, compliance and original metadata separately.
- `sample_id` identifies one original observation within its tuple key. It must
  be unique within that sequence. Reuse in another key is permitted, so the
  full tuple plus `sample_id` is the identity. Distinct sample IDs with equal
  coordinates are retained; duplicate IDs are rejected, never deduplicated.
- All identifiers are opaque strings, 1–128 characters, unpadded and free of
  ASCII control characters. IDs such as `001` and `1` remain distinct. Quoted
  commas and Unicode identifiers are allowed. No ID sorting or normalization
  occurs.
- `coordinate` is one signed decimal/scientific number in the declared unit.
  The original parsed CSV field is retained as `coordinate_text`, a JSON string.
  It is never rounded to a binary64 JSON number. Whitespace, unit suffixes,
  thousands separators, underscores, nonfinite values and hexadecimal numbers
  are rejected.
- Numeric limits: at most 64 characters in the token, 32 digits in the parsed
  Decimal coefficient, and absolute **stored Decimal exponent** at most 200.
  Trailing coefficient zeros count. The exponent is not just the exponent
  suffix: `1.0e-200` has stored exponent `-201` and is rejected, while `1e-200`
  is accepted. These are resource bounds in the declared unit, not an instrument
  range or accuracy claim. No float arithmetic or SI magnitude bound is applied.
- UTF-8 BOM and CRLF line endings are accepted. Blank records, missing/extra
  fields, duplicate/missing/extra header names, malformed CSV, invalid encoding,
  empty files and header-only input are fatal. No rows are silently skipped.
  A final line ending is allowed; an extra empty record is not.
- Only regular files are accepted. Limits are 8 MiB, 10,000 data records and
  1,000 tuple keys. Invalid input fails the entire audit before any report is
  written; there are no excluded or partially accepted sequences.

Keep the complete instrument export unchanged. Prepare this coordinate-only
projection without dropping samples, carry a traceable `sample_id` to the
original record, and retain the original response channels and metadata in the
source file or a documented adapter manifest. This tool cannot recover fields
omitted before it runs. Its hash and source line numbers describe **its input
CSV**, not the original instrument file unless that file is the input.

## Axis and units

Required `--axis` and matching `--coordinate-unit` values:

| Axis | Units | Required interpretation |
| --- | --- | --- |
| `voltage` | `V`, `mV`, `uV`, `nV` | A documented signed terminal voltage, such as VGS or device voltage; reference terminal must be known separately. |
| `magnetic-flux-density` | `T`, `mT`, `uT`, `G`, `kG` | Signed B along the documented axis, not H in A/m. |

One unit applies to every coordinate in the input. No unit is inferred, and
**no numerical unit conversion occurs**. Exact comparisons in a common unit
suffice for direction. A wrong but syntactically accepted unit or sign convention
cannot be detected by this tool. There is no noise threshold, default tolerance,
minimum segment length, denoising, outlier removal or sample-exclusion option.

## Exact segmentation rules

For a sequence of `n` samples, edge `i` joins supplied sample `i` to sample
`i+1`, using one-based local indices. Compare the two Decimal values directly,
without subtraction or rounding:

1. Right coordinate greater than left: `increasing`.
2. Right coordinate less than left: `decreasing`.
3. Exactly equal: `flat`, including `1`, `1.0` and `1e0`, or signed zero.
4. A segment is one **maximal contiguous run of equal edge direction**. Every
   one of the `n-1` edges belongs to exactly one segment. A flat segment is not
   absorbed into either neighboring directional segment.
5. Adjacent segments share exactly one endpoint observation. Samples are stored
   once; segments reference sample indices and the sample lists its segment
   memberships. A coordinate plateau with different sample IDs is multiple
   retained observations, even though the coordinate values compare equal.
6. A singleton is valid: one sample, no edges, no segments, and sample index 1
   listed in `unsegmented_sample_indices`. An all-equal sequence with at least
   two samples is one flat segment.

For `0, 1, 1.0, 0`, edge directions are `increasing, flat, decreasing` and
segment sample references are `[1,2]`, `[2,3]`, `[3,4]`. Samples 2 and 3 are
separate observations with equal coordinates. Each is shared with the flat
segment, and both are preserved as the turnaround interval.

`coordinate_reversals` compares consecutive nonflat runs. Opposite directions
form a reversal either at one shared sample (`direct`) or across the complete
intervening flat run (`across_plateau`). The latter reports every sample index
and identity from the end of the preceding run to the start of the following
run. `0,1,1,2` therefore has three segments but no direction reversal. Leading,
trailing and entirely flat intervals alone do not count as reversals. These
counts describe coordinate geometry only, not why the coordinates changed.

## JSON audit and safe output

Schema version `1` includes:

- `axis`, `coordinate_unit`, tool version, exact-comparison rules and limitations.
- `provenance.input_sha256`: SHA-256 of exact input bytes, including BOM/line
  endings. Record indices count data records excluding the header. Physical
  line ranges include the header; sample and edge indices are local to a sequence.
- `counts`: input samples/sequences, edges, segments, flat edges, shared endpoint
  samples, coordinate reversals and singleton sequences. No independence count
  or scientific pass/fail score is reported.
- `results`: tuple keys in first-appearance order. Each has `samples`,
  `segments`, `shared_endpoints`, `coordinate_reversals`,
  `unsegmented_sample_indices` and explicit warnings.
- Each sample has its original `sample_id`, `coordinate_text`, input record and
  physical-line lineage, local `sample_index` and `segment_ids` memberships.
- Each segment has a local `segment_id`, `direction`, inclusive `edge_start`
  and `edge_end`, `edge_count`, and ordered `sample_indices` references.
  Segment IDs are scoped to the tuple key and are not experimental branch labels.
- Shared endpoints explicitly map the same sample index and ID to both segment
  IDs. Reversal entries name the source/destination segment IDs and the ordered
  turnaround sample indices and IDs. Do not sum segment-reference counts as
  independent observations.

`--stdout` explicitly opts into exposing identifiers, coordinates and lineage on
standard output. `--output` creates a new report exclusively, requesting POSIX
mode `0600`. Existing files, input files, directories and live or dangling
symlinks are never overwritten. Other operating systems' permissions may differ.
Errors redact paths, identifiers and values. A write failure can leave an
incomplete newly created file; discard it after a fatal exit. The input is never
changed and no data are transmitted.

Exit status is **`0` for a completed audit**, including plateaus, singletons or
many reversals; **`2` for fatal input/CLI/I/O errors**. There is no status `1`.
Successful segmentation is not validation for physical interpretation or another
extractor's assumptions.

## What to do with the mapping

Use the audit to locate candidate breaks and inspect the corresponding original
records. Before declaring downstream branches, verify acquisition order and
fixed protocol/conditions from acquisition records. Preserve plateau samples,
shared endpoint identity, any deliberate selection and original response-channel
lineage in a separate reviewed adapter. Do not automatically duplicate the
shared endpoint as independent evidence, assign cycles from reversal counts,
or silently delete flat records to make an extractor pass. This tool makes no
such choices and performs no automatic handoff to the measurement extractors.

## Verification

```sh
python3 -m unittest discover -s reference/python/tools/sweep-segments/tests -v
python3 reference/python/run_tests.py
python3 reference/python/tools/sweep-segments/examples/generate_synthetic.py hall
python3 reference/python/tools/sweep-segments/examples/generate_synthetic.py gate
```

The generators print only original synthetic CSV data. A separate original
three-leg `0 → +70 → -70 → 0 mV` regression checks 281 samples, 280 edges,
70/140/70-edge runs and two shared turnaround observations. Neither path
reproduces a measurement archive. Tests cover both fixture
byte streams, exhaustive short direction paths, edge partitions, shared
identities, direct/plateau reversals, non-reversing plateaus, singletons, every
group dimension, noncontiguous groups, decimal differences below float precision,
extreme exponents, equivalent unit-scaled paths, strict parsing, resource limits,
lineage/hashes, no-output failures and exclusive owner-only output. They establish
software behavior, not semiconductor measurement accuracy or scientific validity.
