# Validation scope: native Rust 0.1.0

Date: 2026-10-24. These are software tests on deterministic synthetic inputs, not experimental validation or a proof over all possible inputs. Counts below describe different, partly overlapping suites and must not be added into a claim of distinct scientific tests.

## Platform evidence

| Artifact | Build/inspection | Actual runtime |
|---|---|---|
| Linux x86-64 musl | Release-optimized, stripped static PIE; no dynamic loader or shared-library dependency | Executed on the Linux cloud host, including all ten tools and both feature workflows |
| Windows x86-64 GNU/UCRT | Real PE32+ AMD64 console EXE, cross-compiled with Rust 1.99.1 and official Zig 0.12.1; imports inspected | **Not executed on Windows or Wine. Windows runtime and ACL behavior remain unverified.** |

The Windows artifact is intended for modern Windows 10/11 x64 with UCRT, not Windows 7. It is unsigned. The Linux artifact does not require Python, Rust, a model download or an API credential to run. Successful native invocations with an empty PATH and source/import inspection establish the native implementation evidence; no syscall-tracing verification is claimed.

## Ten-tool coverage matrix

All ten command implementations are native Rust, not wrappers or stubs. Each is exercised by actual CLI comparisons with the unchanged Python reference. This table counts the captured-reference replay suite only; independent adversarial checks are separate below.

| Command | Native status | Exact captured CLI cases | Scope retained |
|---|---|---:|---|
| `iv-readpoint` | Implemented and Linux-tested | 62 | Signed current, explicit units, adjacent interpolation, no extrapolation, raw row lineage |
| `rram-window` | Implemented and Linux-tested | 74 | Explicit HRS/LRS, exact bias identity, sign/range diagnostic ordering, device/condition summaries |
| `tlm-extract` | Implemented and Linux-tested | 82 | Exact rational fit, physical-fit gates, 50-digit Decimal RMSE context |
| `vth-constant-current` | Implemented and Linux-tested | 178 | Signed criterion, branch order, crossing ambiguity, exact bracket bounds |
| `hall-response` | Implemented and Linux-tested | 172 | Namespaced groups, fixed bias/temperature, exact fit, 50-digit RMSE context |
| `sweep-segments` | Implemented and Linux-tested | 116 | Exact direction, ordered edge runs, shared endpoints and source hashes |
| `transfer-log-slope` | Implemented and Linux-tested | 89 | Certified 80-digit logarithms, exact rational fit, atomic numeric rejection |
| `iv-loop-integral` | Implemented and Linux-tested | 182 | Exact signed trapezoids/closure, outgoing-edge branch ownership, exact-string bounds |
| `bosch-identity-audit` | Implemented and Linux-tested | 18 | Exact identity/channel checks, opaque target/response scanning without retained values |
| `iv-grid-audit` | Implemented and Linux-tested | 78 | Exact arbitrary-decimal grid comparison, opaque response scanning, duplicate/missing lineage |
| **Total captured CLI comparisons** | **No numerical tolerance** | **1,051** | **35 Python-API-only captures explicitly excluded, never counted as passes** |

The 1,086 captures came from running 452 core reference tests. The unchanged full reference suite has 515 tests, including its workflow tests. Passing reference tests is baseline evidence, not native Rust coverage. See [explicit exclusions and their replacements](docs/coverage-exclusions.md).

## Two native workflows

`rram-features` and `fet-features` call their Rust core modules in-process. Both are implemented and Linux-tested. The workflow suite checks 54 native CLI test methods, with exact CSV bytes, exact scientific JSON including signed zero, and only documented native provenance normalization. Eleven native unit tests additionally exercise integrity mismatch, snapshot isolation, cleanup and fail-closed export paths.

An independent actual-Python-CLI/native-CLI workflow suite checks 68 cases. It independently verifies schema-2 compiled-source and emitted-output hashes, original input immutability, audit-only suppression, physical-device identity, predictor row alignment, empty-PATH native execution, private POSIX modes, cleanup and no overwrite. Scientific/core report schemas and feature-schema version 1 are unchanged; workflow provenance deliberately changes to version 2. See [native workflow contracts](docs/NATIVE_WORKFLOWS.md).

## Additional release acceptance

- 44 native Rust unit tests and 3 CLI integration tests, including 512 full-80-digit log reference values within one native test.
- Independent 464-case actual CLI adversarial suite across all ten tools, with exact finite binary64 comparisons including the sign of zero. Deliberate valid-input Hall/TLM midpoint fixtures detect a one-ULP rounding error that generic random cases miss.
- Independent 194 POSIX filesystem/privacy/resource checks. These include actual production input/report bounds, existing/live/dangling symlink outputs, invalid Unicode arguments without leaked values, FIFO rejection, and early malformed-input memory guards.
- 1,098 additional actual TLM/Hall CLI cases, plus 22 safety/help checks.
- 35 transfer adversarial CLI cases, including hundreds of randomized groups in individual cases, exact window bounds, real group/row/byte caps and reachable numeric underflow/overflow rejection.
- Both bundled shell demo scripts executed successfully: ten core commands with tool-specific expected exit codes, plus both complete native feature workflows.

All final binary-bound report summaries are under `validation/release/` in the standalone source archive. Re-runnable independent generators are under `tests/independent/`. The reference remains unchanged; its file hashes and the compiled Rust source hashes are provided separately.

## Honest limitations and compatibility differences

- No tool or workflow remains a Python runtime wrapper. The original Python fixture generators remain development/reference utilities; ready-made synthetic inputs are bundled, and native usage never invokes them.
- No Windows execution, PowerShell execution, Windows ACL test, real measurement validation, hardware integration, performance guarantee or general speedup is claimed.
- Only regular input files and valid UTF-8 command-line arguments are supported. Pipes/devices and non-UTF8 path arguments are rejected categorically. Human CLI error prose is intentionally safe, not byte-identical argparse text.
- POSIX output files request mode 0600, workflow directories 0700. Windows inherits destination directory ACLs and is not promised owner-only access. Output I/O failure may leave a partial newly created file/directory; the tool never overwrites an existing destination. Abrupt termination can leave a temporary snapshot.
- Eight core tools enforce their existing 8-MiB input cap. `iv-readpoint` and `rram-window` deliberately retain the reference's lack of a total byte/row cap, with the standard CSV field-length cap; do not advertise uniform bounded memory for arbitrary large inputs.
- Exact decimal/rational decisions are preserved; binary64 diagnostic values and JSON formatting are not evidence that distinct exact inputs are equal. JSON whitespace, map ordering and exponent spelling need not match Python byte-for-byte.
- Synthetic parity does not establish scientific assumptions, independence, device physics, causal validity or predictive usefulness. No ML model is trained or supplied.

## Reproduce

From a standalone source archive with the official Rust toolchain installed:

```sh
cargo build --release --locked --offline
cargo test --locked --offline
python3 reference/python/run_tests.py
python3 tests/capture_reference.py
python3 tests/differential.py --strict
python3 tests/contact_cli_parity.py --random-cases 500
python3 tests/transfer_adversarial.py
SMLT_BINARY="$PWD/target/release/smlt" python3 tests/workflow_cli_parity.py
python3 tests/independent/review_cli.py
python3 tests/independent/review_safety.py
python3 tests/independent/review_workflows.py
SMLT_BINARY="$PWD/target/release/smlt" sh scripts/run-demo.sh
SMLT_BINARY="$PWD/target/release/smlt" sh scripts/run-workflows.sh
```

For an additive `rust/` Git checkout, omit `--offline` for the first dependency fetch and set `SMLT_PYTHON_REFERENCE=..` for the independent scripts; the other harnesses select the existing parent reference automatically. For another binary, use `--binary` where offered or `SMLT_BINARY` as documented. POSIX-specific safety tests are not Windows runtime tests. Build toolchain URLs, checksums and commands are recorded in release `BUILD_PROVENANCE.json`.
