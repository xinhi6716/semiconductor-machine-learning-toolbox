# Contributing

Keep this toolbox semiconductor-specific. A contribution should explain the
measurement/modeling question it answers and why a generic table utility is
insufficient.

Before proposing a tool or change:

1. Define the input schema, units, terminal/polarity convention, physical
   assumptions, supported numerical range, and rejection behavior.
2. Preserve device, wafer, lot, cycle, and condition provenance where relevant.
   Do not silently equate repeated cycles with independent devices.
3. Include original synthetic fixtures or clearly licensed public data. Never
   commit private measurement data, manuscript/review artifacts, credentials,
   generated raw-data reports, or third-party figures.
4. Add meaningful positive, negative, numerical-boundary, and output-safety tests.
   Prefer Python's standard library for small utilities. Avoid network calls and
   hidden dependencies.
5. Require explicit output destinations, refuse overwrites, and keep diagnostics
   free of raw private identifiers or values. A report containing data must be
   deliberately requested.
6. Document what is measured, what is merely calculated, what is user-declared,
   and what the software cannot establish. Do not advertise validated device
   performance based on fixture tests.
7. Run `python3 run_tests.py` and the documented CLI examples. Independently
   review physical interpretation as well as software behavior before release.

Public APIs and JSON schemas are early-stage and may change. Version schema
changes explicitly; do not silently reinterpret existing output fields.
