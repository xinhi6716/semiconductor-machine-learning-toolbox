# Native transfer measurement port: numerical method and verification

Reference: clean Python working tree 842f9742f02432068e6c4bb6898d7c393fca5968, commit 1f368c2f02a5eb9f33bdefdb3da9072305f2dba6. Reference Vth source SHA-256: 3e54f330ea236d6932cbd4fc5b99a5b3b238b01e8b91141cd0660a2f30d20cf4. Reference log-slope source SHA-256: 2a4914bdfc6d3e84ed16aba5c3fe10404b0299e6c8831f68dc80907480cfe328.

## Implemented contracts

- `vth-constant-current`: explicit signed current criterion, explicit current polarity, exact SI conversion, source-referenced VGS/VDS/VBS, fixed positive temperature, fixed nonzero VDS and fixed VBS. Each complete lot/wafer/device/condition/replicate/branch key remains separate. Acquisition order is not sorted or repaired. Unique exact hits or adjacent strict interior brackets only, monotonic gate/current checks in the Python reference's order, optional inclusive exact bracket-span limit, no extrapolation, no current normalization, no inferred transistor type or physical threshold.
- `transfer-log-slope`: the inclusive user voltage window is selected with exact rational comparisons. Every source row is retained, including unselected nonloggable finite currents. No window search, endpoint interpolation, current trimming, absolute-value sign repair, or subthreshold certification. Equal-row-weight OLS uses exact rational gate coordinates and 80-significant-decimal-digit rounded base-10 logarithms. Signed slope/reciprocal, separately labeled reciprocal magnitude, exact fitted-row lineage, residuals, R-squared and N-denominator RMSE follow the reference.
- Both preserve the bounded UTF-8/CSV/identifier/numeric/group contracts, input SHA-256, source physical end-of-record lines, branch/replicate isolation, categorical rejection reasons, and complete rejected-group audit. Fatal syntax/data validation and rejected scientific groups remain distinct. CLI wiring, output permissions and exit mapping are handled by the shared driver.

## Certified 80-digit logarithms

`transfer_log_slope.rs` does not call binary64 logarithms. For positive exact decimal current v, it finds integer e=floor(log10(v)) using integer digit counts and exact rational comparisons, then reduces m=v/10^e into [1,10). Powers of ten return integer logarithms exactly.

For m in [1,10], set z=(m-1)/(m+1), so 0 <= z <= 9/11, and use

    ln(m) = 2 * sum(k >= 0, z^(2k+1)/(2k+1)).

At decimal fixed-point scale S=10^p, every lower endpoint operation rounds down and every upper endpoint operation rounds up. Positive integer arithmetic encloses z, z^2, each successive odd power and each sum term. If P encloses the next odd power after term k and U encloses z^2, the uncomputed tail in scaled units is bounded above by

    ceil(2 * P_upper * S / ((2*k+3) * (S-U_upper))).

The routine terminates only after this rigorous tail bound is at most one scaled unit. Accumulated rounding interval width remains in the returned lower/upper bounds; it is not discarded or treated as zero. The series iteration limit is explicit and bounded.

Independently bounded ln(10) yields

    ln(m)_lower / ln(10)_upper <= log10(m) <= ln(m)_upper / ln(10)_lower.

Adding e is exact. Both rational endpoints are rounded to 80 significant decimal digits using exact nearest/ties-to-even rounding. A logarithm is accepted only when the rounded endpoints are identical. Thus the actual logarithm must round to the same value. Initial precision is 140 digits, with adaptive increments of 40 digits through an explicit bounded budget below 1024 digits. Unresolved rounding or failure to converge is rejected as `derived_numeric_range_error`, never silently approximated. No tested supported input required increased precision.

The parsed currents have at most 32 source significant digits and exact decimal unit factors. Consequently Python's intermediate Decimal division at precision 80 represents the input exactly, so the accepted value matches its `Decimal.log10()` rounding contract. Regression occurs on those rounded logarithms as exact rationals, including means, centered products, fit coefficients, residuals, SSE and R-squared. Only final report numbers are converted to binary64. Nonzero underflow/overflow is rejected. The diagnostic constant-current interpolation weight is the deliberate exception: it may round to 0 or 1, while the independently converted exact threshold remains valid and receives the specified warning.

## Prescribed RMSE decimal contexts

The reference rounds the rational mean square by Decimal division, then rounds its square root, both at 50 significant digits for TLM/Hall and 80 for transfer log-slope. Using a seemingly more accurate default 100-digit context can change a final binary64 result at a rounding midpoint. The native helper therefore computes the division's exact integer quotient/remainder at the required decimal scale, rounds nearest/ties-to-even, then takes the square root with the same explicit context. Conversion to binary64 is from the resulting decimal string, avoiding an additional coefficient/exponent double rounding. Both abstract midpoint regressions and actual valid 32-significant-digit Hall/TLM fixtures are checked.

## Executed verification

- Unchanged original Python tests: Vth 51/51 and transfer-log-slope 50/50 pass.
- Captured original-test cases executed through the actual native CLI: 178 Vth + 89 log-slope = 267/267 exact parsed JSON equality and matching exits. No floating tolerance. Eight captured API-only cases are explicitly excluded in the shared corpus: two non-string Python argument cases, four monkeypatched resource-limit cases, a monkeypatched log precision and a monkeypatched arithmetic failure. They are not counted as native passes.
- Native Rust unit tests: three Vth tests and five log-slope tests. These include all-80-digit exact rational equality for 512 deterministic synthetic logarithm inputs, 32-digit near-unity inputs, exponents near supported extremes, decimal ties-to-even, constant-current diagnostic-weight underflow in both acquisition directions, exact-hit span behavior, multiple-crossing precedence, and reachable numeric failure with no partial fit.
- `tests/transfer_adversarial.py` executes the actual Python CLI and native Rust CLI on 35 deterministic synthetic cases with exact full JSON and exit comparison. It includes hundreds of grouped randomized sweeps, both declared polarities, exact near-equal gate windows, retained excluded zero/wrong-sign currents, logarithm rounding residuals, extreme cancellation, truly reachable slope underflow/reciprocal overflow, malformed/nonfinite inputs, multiline CRLF lineage, Python whitespace semantics, 1000 accepted groups, 1001 rejected groups, 1000 accepted group rows, 1001 rejected group rows, >10000 total rows, and >8 MiB inputs. All 35 pass.
- Detailed machine-readable results: `validation/differential-vth-constant-current-transfer-log-slope.strict.json` and `validation/transfer-adversarial/report.json`.

## Differences and limits

- No known supported-input JSON or decision differences in executed tests. Safe fatal diagnostic wording is categorical and may differ from Python; no identifiers, raw values or input paths are copied into errors.
- This is a native scientific utility port, not a trained ML model and not validation on real instruments or private data. Synthetic/reference equivalence establishes software behavior, not physical validity.
- Rust log fitting can be slower than Python's optimized Decimal implementation because it certifies full logarithm error intervals. For the sampled 220-group randomized cases, the Rust run took about 1.7–2.0 seconds versus Python 0.5–0.6 seconds on this host; do not advertise a blanket speed improvement. Vth was faster in the sampled cases. These timings are not a controlled benchmark.
- Compile-time Python monkeypatches/non-string direct API calls are not supported CLI features. The production Rust executable requires no Python interpreter or network.
