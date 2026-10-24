# BOSCH Identity and Channel-Name Audit

Audit the identity structure of the **BOSCH plasma-etching dataset v1** before
scientific numeric decoding or model preparation. Inventory the four identity
columns of its nine-point CSV, preserve empty and duplicate identities, and
compare explicitly declared process-channel labels by **exact name**, not column
position. This is a dataset-specific semiconductor process-preparation tool,
not a general CSV merger or an etch-depth predictor.

Python 3.10+ source, standard library only. No download, network, native NetCDF
dependency, numerical sensor/target decoding, model fit or automatic exclusion.
The distributed inputs are original invented software fixtures, not measurements.

## Quick start

From the repository root:

```sh
python3 tools/bosch-identity-audit/bosch_identity_audit.py \
  tools/bosch-identity-audit/examples/synthetic_inventory.csv \
  --channel-metadata tools/bosch-identity-audit/examples/synthetic_channels.json \
  --stdout
```

Expected exit: `0`, meaning **a complete metadata audit**, not a clean dataset.
The invented example retains 21 rows, including one all-null identity row, one
partial-null row and an exact duplicate. It has two complete three-field keys
with name candidates, plus one process group without a complete-key candidate.
The first key has a repeated location. All of these remain visible.

Three sidecar groups have 44, 31 and 31 labels. One common label was deliberately
replaced in the third group: the report shows 30 common names, 45 union names,
and a missing canonical label with a null column index. The second group's
channel order is reversed. No group is removed or positionally truncated.
These counts describe only the invented fixture, never the real dataset.

Replace `--stdout` with `--output NEW_REPORT.json` for an exclusive new file.
Exactly one destination is required. The requested POSIX mode is `0600`; no
existing file, directory or symlink is overwritten. Other operating systems may
differ. A write failure can leave a partial new file, which is not a completed
report. Output deliberately includes raw identity and channel-name text: keep it
out of public logs when using private inputs. Errors omit source values/paths.

## Use with a locally obtained BOSCH v1 bundle

1. Obtain and retain the dataset under its license yourself. This program does
   not acquire files, accept terms or contact the provider. Preserve originals.
2. Supply the local `Si_Oxide_etch_9_points.csv` without moving/reordering fields.
   This tool opens and hashes its complete bytes, but only decodes the header
   and four identity columns. It traverses other bytes for CSV framing. It is
   **not a byte-blind or OS-level privacy sandbox**.
3. Prepare a **label-only JSON sidecar** from an independently inspected
   `Process_data.nc`: each exact one-level group name and its ordered `feature`
   strings, the 31 canonical names in an explicitly chosen order, and the
   process-file SHA256 recorded by that inspection. Do not include `data`,
   `times`, numeric dictionaries, metrology values, workbook cells or private
   notes. This release does **not** create a NetCDF export or verify the sidecar
   against a native file. Producing/verifying that sidecar is a separate step.
4. Run the same command with those two local paths and a new output destination.
5. Review incomplete keys, duplicates, conflicting name candidates, locations
   and missing canonical labels before designing any later numeric cohort.
   Do not keep deleting records until the report looks clean.

The real-data command shape is:

```sh
python3 tools/bosch-identity-audit/bosch_identity_audit.py \
  /path/to/Si_Oxide_etch_9_points.csv \
  --channel-metadata /path/to/independently-inspected-channel-labels.json \
  --output /path/to/new-identity-audit.json
```

Paths above are placeholders, not bundled files. Nothing here establishes that
you have permission to read a particular dataset or publish its reports.

## Identity input contract

The exact case-sensitive CSV header, in this order, is:

```csv
experiment_key,lot_number,wafer_number,loc_id,X,Y,preox_thickness,postox_thickness,stepheight,oxide_etch,si_etch
```

- Only the first four body fields are UTF-8 decoded. Empty means JSON `null`,
  and only **exact empty text** does so. Spaces and spelling are retained.
  IDs are not stripped, case-folded, integer-normalized, filled or imputed.
- All rows remain in input order, with one-based **logical data-row ordinals
  excluding the header**. Quoted newlines in opaque fields do not create rows.
  No blank record, malformed quote, extra/missing field or header variant is
  silently skipped. LF/CRLF, optional UTF-8 BOM and an absent final newline work.
- Identity text is at most 256 UTF-8 bytes. Invalid UTF-8 and Unicode control
  category `Cc` are fatal. Quoted commas and doubled quotes are supported.
  Spaces, other Unicode codepoints and distinctions such as `001` versus `1`
  are preserved. JSON output is not a spreadsheet-safe CSV export.
- Unselected fields are opaque: even invalid UTF-8 or nonnumeric tokens do not
  become values. Their quoting and record width still must be valid. There is
  no completeness, finite-value, range, formula/sign, unit or quality check on
  `si_etch` or any other measurement column.
- Complete `(experiment_key, lot_number, wafer_number)` strings define reported
  identity keys, **not verified physical wafers**. Incomplete rows stay separate
  and unassigned, including all-null rows. Exact duplicate four-field tuples
  retain all row ordinals. Duplicate location labels are reported, not merged.

### Name candidates, not proven joins

For a valid calendar-date key `YYYY-MM-DD_NN`, with nonzero two-digit `NN`,
the corresponding process-name candidate is `/Day_YYYY_MM_DD_Wafer_NN`.
The raw `wafer_number` must equal the unpadded decimal spelling of that suffix:
`2099-01-02_01` expects text `1`, not `01`, `1.0` or padded text. This comparison
does not rewrite the original value. Invalid/unsupported keys and mismatches
remain in the report without a claimed match.

If distinct complete keys, for example different lot labels, point to the same
process name, every candidate is marked ambiguous. Repeated rows within a key
are separately reported and do not create extra independent wafers. This tool
does not read `Lot_status.xlsx` or verify lot/date/reset/conditioning consistency.
Same-day or same-lot labels are not evidence of exogenous environments.

## Channel sidecar contract

The strict UTF-8 JSON object has exactly four top-level fields:

- `schema_version`: literal `bosch-channel-metadata-v1`.
- `process_file_sha256`: 64 lowercase hexadecimal characters, a **user-declared**
  source fingerprint. The referenced native file is not opened or hashed here.
- `canonical_labels`: exactly 31 distinct nonempty names in the desired output
  order. This is an explicit naming declaration, not proof of valid predictors.
- `groups`: 1–128 objects, each with exactly `group_path` and `feature_labels`.
  Group paths follow the name grammar above and are unique. Ordered label lists
  have width 31 or 44, contain distinct nonempty strings, and preserve every
  source position. Names use the same byte/control limits as identities.

The [invented sidecar](examples/synthetic_channels.json) is a complete format
example. Its all-zero digest is deliberately a dummy, not a verified file hash.
No real channel labels, source payloads or resulting scientific reports are
distributed. Duplicate JSON keys, extra fields, arrays under unapproved keys,
NaN/Infinity constants, duplicate labels and malformed group names are fatal.

For each group, `canonical_name_column_map` follows the declared canonical
order, using zero-based source positions and `null` for absent names.
`missing_canonical_labels` and `noncanonical_labels_in_source_order` are explicit.
The separate common/union summaries use Unicode codepoint lexical ordering and
exact spelling, including case/whitespace. Empty intersection retains all groups.
No label is normalized, no 44-column record is cut to its first 31 positions,
and no channel values are selected or summarized by this audit.

Matching labels do not prove identical units, calibration, instrument wiring,
control/setpoint roles, timing, process recipe or measurement meaning. A sidecar
hash gives reproducible bytes, not authenticity or completeness of its export.

## Report, limits and exit codes

`schema_version = bosch-identity-audit-v1` identifies this report contract.
Top-level sections are `provenance`, `identity_inventory`, `channel_alignment`
and `limitations`. Input SHA256s and byte counts bind the supplied files;
the process hash is explicitly labeled unverified. Every location and row
membership is metadata only, not evidence of nine finite/usable target values.

Limits: 8 MiB CSV, 2 MiB sidecar, 2,000 data rows, 65,536 bytes per CSV logical
record, 256 UTF-8 bytes per identity/label, 128 process groups, and 16 MiB complete
serialized JSON. Capacity failure is fatal, not a partial-cohort export. Inputs
must be regular files, opened nonblocking before a file-type check; input symlinks
may resolve to regular files. Inputs are not modified. These are computational
limits, not assertions about measurement or cohort validity.

- `0`: complete audit, including any nulls, duplicates, missing labels or unmatched
  names. Not a scientific quality score, finite-target gate or fit authorization.
- `2`: fatal input/schema/CLI/resource/output error. No complete report claimed.
  There is no exclusion-status `1` because this audit never selects a cohort.

## Verification

```sh
python3 -m unittest discover -s tools/bosch-identity-audit/tests -v
python3 run_tests.py
```

Tests cover invented nullable and duplicate rows, opaque invalid-UTF8 outcomes,
CSV quotes/newlines, exact label maps, missing labels, ambiguous name candidates,
resource limits, deliberate output and no-overwrite behavior. To regenerate the
original fixtures in a **new** directory:

```sh
python3 tools/bosch-identity-audit/examples/generate_synthetic.py \
  --output-dir /tmp/new-invented-bosch-inputs
```

Only source-free software behavior is tested. No real dataset numeric validation,
prediction performance, physical independence or causal identification is claimed.

## Attribution and rights

Dataset reference: Sayyed et al. (2025), *A Multi-Model Dataset for BOSCH
Plasma-Etching: Optical Emission Spectra, Process Parameters, and Wafer
Measurements for Data-Driven Plasma Modeling*, v1,
[Zenodo DOI 10.5281/zenodo.17122442](https://doi.org/10.5281/zenodo.17122442).
The dataset is offered under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The file/field naming conventions identify that source. No original study code,
data files, figures, full paper text or provider-written documentation are copied.
This adapter is independent and is not endorsed by the dataset authors.

The software, documentation and invented fixtures here are original contributions
under this repository's [MIT license](../../LICENSE). That does not relicense the
dataset. If you redistribute its data or adapted dataset content, retain the
provider's attribution, source/license links and change notices as required.
