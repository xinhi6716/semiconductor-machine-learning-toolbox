# Coverage scope and explicit API-only exclusions

The captured reference corpus contains 1,086 API invocations taken while running the 452 core Python tests. Exactly 1,051 are replayable as the actual native CLI contract. Thirty-five are excluded explicitly, not counted as native passes; `validation/differential-all.strict.json` in the development evidence identifies each index and original test.

Excluded categories are covered or scoped as follows:

- Python monkeypatches of byte, row, group, group-row, column and record constants: native real-limit CLI matrices exercise production limits rather than changing constants. Native parser unit tests verify early record/field bounds and large skipped-blank behavior.
- Python monkeypatches of report byte caps: native `output_bound_rejects_before_creating_file` verifies rejection before any file is opened. Native serialization uses exact tool-specific caps, including ASCII escaping for BOSCH/grid.
- Python monkeypatch of a square-root helper to force a failure: native transfer tests use reachable nonzero underflow/overflow inputs and verify that no partial fit remains.
- Python changing log precision from 80 to 2: impossible in the fixed 80-digit CLI. Native tests compare all 80 digits on 512 independent deterministic inputs and test near-equal currents. The precision is not silently reduced to mimic an unsupported monkeypatch.
- Python non-string argument objects or non-bool callable API arguments: not applicable to the typed CLI. Native missing/invalid options, explicit assumption flags and non-UTF8 argv privacy are tested separately.
- Native I–V integral tests exercise 4096-bit rational and 1024-character exact-string guards, independently of fixture monkeypatching.

These are test-scope distinctions, not claims that arbitrary Python object APIs have been recreated in Rust. CLI diagnostics deliberately use safe categorical wording rather than byte-identical argparse messages. Numeric values, decision gates, rejection reasons, warnings, nullability, source hashes and lineage are compared structurally, with no float tolerance in the release acceptance run. Signed zero is checked separately because ordinary Python equality treats positive/negative zero as equal.
