# Semiconductor Machine Learning Toolbox: native Rust

A native, offline Rust implementation of all ten semiconductor measurement CLIs. The executable does not launch or embed Python, download models, access the network, infer scientific validity, or overwrite input files.

Start with [繁體中文 Quickstart](QUICKSTART_zh-TW.md). Run `smlt --help` and `smlt <tool> --help` for exact schemas/options. All bundled examples are synthetic software-test fixtures, not laboratory evidence.

## Ten core commands

`iv-readpoint`, `rram-window`, `tlm-extract`, `vth-constant-current`, `hall-response`, `sweep-segments`, `transfer-log-slope`, `iv-loop-integral`, `bosch-identity-audit`, `iv-grid-audit`.

The Rust port preserves explicit units, namespaced identity keys, ordered branches, exact decimal comparisons, rejection/warning distinctions, raw-row lineage, opaque response-field handling, and exclusive output creation. It retains tool-specific scientific limitations. A status 0 is not a generic scientific-quality rating.

## Native end-to-end feature workflows

`rram-features` and `fet-features` use the native extraction modules and preserve complete-cohort gates, lineage, predictor allowlists and exclusive multi-file output. Their provenance schema is intentionally version 2 and identifies compiled Rust sources rather than claiming Python execution. See [native workflow contracts](docs/NATIVE_WORKFLOWS.md).

## Quick example

```sh
./smlt iv-readpoint examples/iv-readpoint/synthetic_iv.csv \
  --voltage-unit V --current-unit uA \
  --read-voltage 100 --read-voltage-unit mV \
  --output NEW-readpoint-report.json
```

Windows PowerShell: replace `./smlt` with `.\smlt.exe` and place the command on one line.

- `--stdout` explicitly opts into printing full identities/measurements.
- `--output NEW.json` never overwrites an existing path, including live/dangling symlinks.
- POSIX reports request 0600. Windows permissions inherit directory ACLs and are not claimed to be owner-only.
- Exit 0/1 follow each tool's original checks; exit 2 means fatal input/CLI/I/O failure. A failed new write may leave a partial file; discard it.
- Rust requires regular input files and valid UTF-8 command-line arguments; categorical errors do not echo paths or raw values.

## Scientific and numerical limits

This is descriptive preprocessing, not a trained model, instrument driver, device simulator, automatic state classifier, or experimentally validated method. Do not infer independent devices from repeated records, causation from features, or energy from the I–V path integral.

Exact decimal/rational arithmetic uses arbitrary-precision integers, not a fixed 96-bit decimal. Decision gates do not silently use binary64. Where the reference contract explicitly uses binary64 inputs (I–V readpoint), that behavior is preserved. Transfer logarithms are rounded to 80 significant decimal digits using certified integer intervals, then fitted with exact rational arithmetic. RMSE uses the prescribed 50-digit contact/Hall and 80-digit transfer Decimal contexts before binary64 conversion. Numeric range failures remain rejections rather than clipping/filling.

See `docs/reference/` and `docs/numerical-methods.md` for detailed contracts. `VALIDATION.md` distinguishes native tests, source-reference tests, cross-compilation and actual runtime evidence. No universal speed claim is made; high-precision logarithmic fitting can be slower than Python Decimal.

## Build and reproduce

```sh
cargo build --release --locked
cargo test --locked
python3 tests/capture_reference.py
python3 tests/differential.py --strict
```

Only the reference-comparison test harness needs Python. The original reference is preserved under `reference/python/` in the standalone source package and is never called by the native executable. In a Git checkout, the comparison harnesses use the unchanged Python tree one directory above `rust/`. The standalone source archive includes vendored locked dependencies and can build with `cargo build --release --locked --offline`; a Git checkout uses the standard registry. `Cargo.lock` fixes dependency versions/checksums. Dependency and toolchain notices are included under `licenses/` in each release archive.

Native Windows builds can use the standard Rust MSVC toolchain with official C++ Build Tools. The shipped Windows binary is a verified x86-64 PE cross-build, not a Windows-runtime-tested result. Static Linux musl runtime evidence is recorded separately. Exact commands/toolchain URLs and hashes are in `BUILD_PROVENANCE.json` and `scripts/`.

## Reference provenance

Public Python base: `xinhi6716/semiconductor-machine-learning-toolbox`, commit `355f88026a8fd9fb8d642bf51ffdc3932da49417`.

The supplied local snapshot commit is `1f368c2f02a5eb9f33bdefdb3da9072305f2dba6`. Both identify Git tree `842f9742f02432068e6c4bb6898d7c393fca5968`. The distinction is preserved: the local commit is not represented as the public commit. Per-source-file hashes accompany the release.
