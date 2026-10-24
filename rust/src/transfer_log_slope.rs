//! Descriptive transfer log-current fits in the exact user-selected voltage window.
//! Logarithms are rounded to 80 significant decimal digits from certified integer
//! interval bounds; regression on those rounded values uses exact rationals.
use crate::common::*;
use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

const GROUP: &[&str] = &["lot", "wafer", "device", "condition", "replicate", "branch"];
const COLUMNS: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "replicate",
    "branch",
    "temperature_K",
    "drain_voltage",
    "body_voltage",
    "gate_voltage",
    "drain_current",
];
const VOLTAGE: &[&str] = &["V", "mV", "uV"];
const CURRENT: &[&str] = &["A", "mA", "uA", "nA", "pA"];
const LOG_PRECISION: i64 = 80;
const INITIAL_GUARD_PRECISION: u32 = 140;

#[derive(Clone, Debug)]
struct Point {
    source_row: usize,
    temperature: Rat,
    vds: Rat,
    vbs: Rat,
    vgs: Rat,
    current: Rat,
    input_numeric_text: Value,
}

fn load(
    a: &Args,
    voltage_factor: &Rat,
    current_factor: &Rat,
) -> Res<(IndexMap<Vec<String>, Vec<Point>>, usize, String)> {
    let (rows, digest) = load_rows(&a.input, COLUMNS, true)?;
    let count = rows.len();
    let mut groups: IndexMap<Vec<String>, Vec<Point>> = IndexMap::new();
    for row in rows {
        let key = group_key(&row, GROUP)?;
        let point = Point {
            source_row: row.line_end,
            temperature: number(row.get("temperature_K"), &rat(1))?,
            vds: number(row.get("drain_voltage"), voltage_factor)?,
            vbs: number(row.get("body_voltage"), voltage_factor)?,
            vgs: number(row.get("gate_voltage"), voltage_factor)?,
            current: number(row.get("drain_current"), current_factor)?,
            input_numeric_text: Value::Object(
                COLUMNS[6..]
                    .iter()
                    .map(|name| (name.to_string(), json!(row.get(name))))
                    .collect(),
            ),
        };
        let points = groups.entry(key).or_default();
        points.push(point);
        if points.len() > 1000 || groups.len() > 1000 {
            return err("CSV exceeds row or group limits");
        }
    }
    Ok((groups, count, digest))
}

fn selected(p: &Point, lower: &Rat, upper: &Rat) -> bool {
    lower <= &p.vgs && &p.vgs <= upper
}
fn observation(p: &Point, lower: &Rat, upper: &Rat, sign: &Rat) -> Res<Value> {
    let selection = if &p.vgs < lower {
        "below_user_window"
    } else if &p.vgs > upper {
        "above_user_window"
    } else {
        "inside_user_window"
    };
    let domain = if p.current.is_zero() {
        "zero_current"
    } else if (&p.current * sign).is_negative() {
        "polarity_conflict"
    } else {
        "positive_declared_polarity_current"
    };
    Ok(
        json!({"source_row":p.source_row, "input_numeric_text":p.input_numeric_text,
        "gate_source_voltage_V":finite(&p.vgs)?, "drain_current_A":finite(&p.current)?,
        "drain_source_voltage_V":finite(&p.vds)?, "body_source_voltage_V":finite(&p.vbs)?,
        "temperature_K":finite(&p.temperature)?, "window_selection":selection,
        "current_log_domain":domain,"used_in_fit":false}),
    )
}
fn reject(mut result: Value, reason: &str) -> Value {
    result["reason"] = json!(reason);
    result
}
fn warn(result: &mut Value, warning: &str) {
    result["warnings"]
        .as_array_mut()
        .unwrap()
        .push(json!(warning));
}
fn float_merges(values: &[&Rat]) -> Res<bool> {
    let exact: BTreeSet<&Rat> = values.iter().copied().collect();
    let floats: HashSet<u64> = values
        .iter()
        .map(|v| finite(v).map(f64::to_bits))
        .collect::<Res<_>>()?;
    Ok(exact.len() != floats.len())
}

/// floor(log10(abs(value))) with exact integer comparisons, never binary64.
fn decimal_order(value: &Rat) -> i64 {
    let value = value.abs();
    assert!(!value.is_zero());
    let mut e = value.numer().to_string().len() as i64 - value.denom().to_string().len() as i64;
    if value < ten(e) {
        e -= 1;
    }
    while value >= ten(e + 1) {
        e += 1;
    }
    e
}

/// Correct nearest, ties-to-even rounding of an exact rational to decimal digits.
fn round_significant(value: &Rat, digits: i64) -> Rat {
    if value.is_zero() {
        return rat(0);
    }
    let exponent = decimal_order(value);
    let factor = ten(digits - 1 - exponent);
    let scaled = value.abs() * &factor;
    let mut q = scaled.numer() / scaled.denom();
    let remainder = scaled.numer() % scaled.denom();
    let twice = remainder * 2;
    if twice > *scaled.denom() || (twice == *scaled.denom() && &q % 2 == BigInt::one()) {
        q += 1;
    }
    if value.is_negative() {
        q = -q;
    }
    Rat::from_integer(q) / factor
}
fn ceil_positive(n: &BigInt, d: &BigInt) -> BigInt {
    (n + d - BigInt::one()) / d
}

/// Enclose ln(x)*10^precision using ln(x)=2*atanh((x-1)/(x+1)).
/// x is in [1,10]. Every lower operation is floor, every upper operation is
/// ceiling. The remaining positive series is bounded by a geometric tail.
/// These are certified error intervals, not a heuristic convergence tolerance.
fn ln_bounds(x: &Rat, precision: u32) -> Res<(BigInt, BigInt)> {
    if x < &rat(1) || x > &rat(10) {
        return err("logarithm range-reduction error");
    }
    if x == &rat(1) {
        return Ok((BigInt::zero(), BigInt::zero()));
    }
    let scale = BigInt::from(10).pow(precision);
    let z = (x - rat(1)) / (x + rat(1));
    let scaled_numerator = z.numer() * &scale;
    let zlo = &scaled_numerator / z.denom();
    let zhi = ceil_positive(&scaled_numerator, z.denom());
    let z2lo = &zlo * &zlo / &scale;
    let z2hi = ceil_positive(&(&zhi * &zhi), &scale);
    let one_minus_z2hi = &scale - &z2hi;
    if one_minus_z2hi <= BigInt::zero() {
        return err("logarithm interval range error");
    }
    let (mut plo, mut phi) = (zlo, zhi);
    let (mut sumlo, mut sumhi) = (BigInt::zero(), BigInt::zero());
    for k in 0..(precision as usize * 12 + 100) {
        let odd = BigInt::from(2 * k + 1);
        sumlo += &plo / &odd;
        sumhi += ceil_positive(&phi, &odd);
        plo = &plo * &z2lo / &scale;
        phi = ceil_positive(&(&phi * &z2hi), &scale);
        let next_odd = BigInt::from(2 * k + 3);
        let tail = ceil_positive(
            &(BigInt::from(2) * &phi * &scale),
            &(next_odd * &one_minus_z2hi),
        );
        if tail <= BigInt::one() {
            return Ok((sumlo * 2, sumhi * 2 + tail));
        }
    }
    err("logarithm interval did not converge within the bounded arithmetic budget")
}
static LN10_INITIAL: LazyLock<(BigInt, BigInt)> = LazyLock::new(|| {
    ln_bounds(&rat(10), INITIAL_GUARD_PRECISION).expect("constant logarithm interval")
});

/// Match Python Decimal.log10 with precision=80 and ROUND_HALF_EVEN. Inputs
/// are exact finite decimal currents with at most 32 source significant digits.
fn log10_fraction(value: &Rat) -> Res<Rat> {
    if value <= &rat(0) {
        return err("logarithm requires positive declared-polarity current");
    }
    let exponent = decimal_order(value);
    let mantissa = value / ten(exponent);
    if mantissa == rat(1) {
        return Ok(rat(exponent));
    }
    let mut precision = INITIAL_GUARD_PRECISION;
    while precision <= 1024 {
        let (lo, hi) = ln_bounds(&mantissa, precision)?;
        let (ln10lo, ln10hi) = if precision == INITIAL_GUARD_PRECISION {
            LN10_INITIAL.clone()
        } else {
            ln_bounds(&rat(10), precision)?
        };
        let lower = rat(exponent) + Rat::new(lo, ln10hi);
        let upper = rat(exponent) + Rat::new(hi, ln10lo);
        let rounded_lower = round_significant(&lower, LOG_PRECISION);
        let rounded_upper = round_significant(&upper, LOG_PRECISION);
        if rounded_lower == rounded_upper {
            return Ok(rounded_lower);
        }
        precision += 40;
    }
    err("logarithm rounding could not be certified within the bounded arithmetic budget")
}

struct FitOutcome {
    fit: Value,
    reciprocal: Value,
    fitted: Vec<Value>,
    sse: Rat,
    syy: Rat,
    slope: Rat,
}
fn fit_points(points: &[&Point], sign: &Rat) -> Res<FitOutcome> {
    let x: Vec<&Rat> = points.iter().map(|p| &p.vgs).collect();
    let current: Vec<Rat> = points.iter().map(|p| &p.current * sign).collect();
    let mut log_cache: IndexMap<Rat, Rat> = IndexMap::new();
    let mut y = Vec::with_capacity(current.len());
    for v in &current {
        let log = if let Some(log) = log_cache.get(v) {
            log.clone()
        } else {
            let log = log10_fraction(v)?;
            log_cache.insert(v.clone(), log.clone());
            log
        };
        y.push(log);
    }
    if current.iter().collect::<BTreeSet<_>>().len() != y.iter().collect::<BTreeSet<_>>().len() {
        return err("log_precision_merges_distinct_currents");
    }
    let n = rat(x.len() as i64);
    let x_mean = x.iter().copied().fold(rat(0), |a, b| a + b) / &n;
    let y_mean = y.iter().fold(rat(0), |a, b| a + b) / &n;
    let dx: Vec<Rat> = x.iter().map(|v| *v - &x_mean).collect();
    let dy: Vec<Rat> = y.iter().map(|v| v - &y_mean).collect();
    let sxx = dx.iter().fold(rat(0), |a, v| a + v * v);
    if sxx.is_zero() {
        return err("rank_deficient_gate_voltage");
    }
    let slope = dx.iter().zip(&dy).fold(rat(0), |a, (x, y)| a + x * y) / &sxx;
    let intercept = &y_mean - &slope * &x_mean;
    let predicted: Vec<Rat> = dx.iter().map(|v| &y_mean + &slope * v).collect();
    let residuals: Vec<Rat> = y.iter().zip(&predicted).map(|(a, b)| a - b).collect();
    let sse = residuals.iter().fold(rat(0), |a, v| a + v * v);
    let syy = dy.iter().fold(rat(0), |a, v| a + v * v);
    let max_residual = residuals.iter().map(Signed::abs).max().unwrap();
    let fit = json!({
        "method":"unweighted_ordinary_least_squares_log10_current_on_vgs",
        "slope_decades_per_V":finite(&slope)?,
        "zero_gate_intercept_log10_current_over_A":finite(&intercept)?,
        "mean_gate_source_voltage_V":finite(&x_mean)?,
        "mean_log10_current_over_A":finite(&y_mean)?,
        "r_squared":if syy.is_zero() {Value::Null} else {json!(finite(&(rat(1)-&sse/&syy))?)},
        "rmse_decades":sqrt_rat_precision(&(&sse/&n),80)?,
        "max_abs_residual_decades":finite(&max_residual)?,
        "selected_gate_voltage_range_V":[finite(x.iter().min().unwrap())?,finite(x.iter().max().unwrap())?],
        "selected_gate_voltage_span_V":finite(&(*x.iter().max().unwrap()-*x.iter().min().unwrap()))?,
        "selected_signed_current_range_A":[finite(points.iter().map(|p|&p.current).min().unwrap())?,finite(points.iter().map(|p|&p.current).max().unwrap())?],
        "observed_log10_current_span_decades":finite(&(y.iter().max().unwrap()-y.iter().min().unwrap()))?
    });
    let reciprocal = if slope.is_zero() {
        Value::Null
    } else {
        json!({
            "signed_V_per_decade":finite(&(rat(1)/&slope))?,
            "signed_mV_per_decade":finite(&(rat(1000)/&slope))?,
            "magnitude_mV_per_decade":finite(&(rat(1000)/&slope).abs())?,
            "interpretation":"reciprocal_descriptive_slope_not_certified_subthreshold_swing"
        })
    };
    let fitted: Vec<Value> = y.iter().zip(&predicted).zip(&residuals).map(|((actual,fitted),residual)| -> Res<Value> {
        Ok(json!({"log10_declared_polarity_current_over_A":finite(actual)?,"fitted_log10_current_over_A":finite(fitted)?,"residual_decades":finite(residual)?}))
    }).collect::<Res<_>>()?;
    Ok(FitOutcome {
        fit,
        reciprocal,
        fitted,
        sse,
        syy,
        slope,
    })
}

fn extract(key: &[String], points: &[Point], lower: &Rat, upper: &Rat, sign: &Rat) -> Res<Value> {
    let selected_points: Vec<&Point> = points
        .iter()
        .filter(|p| selected(p, lower, upper))
        .collect();
    let mut result = identity(GROUP, key);
    result["status"] = json!("rejected");
    result["sample_count"] = json!(points.len());
    result["source_rows"] = json!(points.iter().map(|p| p.source_row).collect::<Vec<_>>());
    result["selected_sample_count"] = json!(selected_points.len());
    result["selected_source_rows"] = json!(selected_points
        .iter()
        .map(|p| p.source_row)
        .collect::<Vec<_>>());
    result["outside_window_source_rows"] = json!(points
        .iter()
        .filter(|p| !selected(p, lower, upper))
        .map(|p| p.source_row)
        .collect::<Vec<_>>());
    result["observations"] = json!(points
        .iter()
        .map(|p| observation(p, lower, upper, sign))
        .collect::<Res<Vec<_>>>()?);
    result["fit"] = Value::Null;
    result["reciprocal_slope"] = Value::Null;
    result["warnings"] = json!([
        "user_window_not_certified_subthreshold",
        "descriptive_log_fit_not_physical_model_validation",
        "branches_and_replicates_not_independent_devices"
    ]);
    if points.iter().any(|p| p.temperature <= rat(0)) {
        return Ok(reject(result, "nonpositive_temperature"));
    }
    if points.iter().any(|p| p.vds.is_zero()) {
        return Ok(reject(result, "zero_drain_bias"));
    }
    let p0 = &points[0];
    if points
        .iter()
        .any(|p| p.temperature != p0.temperature || p.vds != p0.vds || p.vbs != p0.vbs)
    {
        return Ok(reject(result, "inconsistent_temperature_or_bias"));
    }
    result["conditions"] = json!({"temperature_K":finite(&p0.temperature)?, "drain_source_voltage_V":finite(&p0.vds)?, "body_source_voltage_V":finite(&p0.vbs)?});
    let voltages: Vec<&Rat> = points.iter().map(|p| &p.vgs).collect();
    if voltages.iter().copied().collect::<BTreeSet<_>>().len() != voltages.len() {
        return Ok(reject(result, "duplicate_gate_voltage"));
    }
    if points.len() < 3 {
        return Ok(reject(result, "fewer_than_three_branch_samples"));
    }
    let ascending = voltages.windows(2).all(|w| w[1] > w[0]);
    let descending = voltages.windows(2).all(|w| w[1] < w[0]);
    if !(ascending || descending) {
        return Ok(reject(result, "nonmonotonic_gate_voltage"));
    }
    result["acquisition_direction"] = json!(if ascending {
        "increasing_vgs"
    } else {
        "decreasing_vgs"
    });
    let min_voltage = *voltages.iter().min().unwrap();
    let max_voltage = *voltages.iter().max().unwrap();
    result["branch_gate_voltage_range_V"] = json!([finite(min_voltage)?, finite(max_voltage)?]);
    if lower < min_voltage || upper > max_voltage {
        warn(
            &mut result,
            "requested_window_extends_beyond_measured_branch_no_extrapolation",
        );
    }
    if points
        .iter()
        .any(|p| !selected(p, lower, upper) && &p.current * sign <= rat(0))
    {
        warn(
            &mut result,
            "outside_window_nonloggable_current_retained_not_fitted",
        );
    }
    if selected_points.len() < 3 {
        return Ok(reject(result, "fewer_than_three_samples_in_user_window"));
    }
    if selected_points.iter().any(|p| p.current.is_zero()) {
        return Ok(reject(result, "zero_current_in_user_window"));
    }
    if selected_points
        .iter()
        .any(|p| (&p.current * sign).is_negative())
    {
        return Ok(reject(
            result,
            "current_sign_conflicts_with_declared_polarity_in_user_window",
        ));
    }
    if float_merges(&voltages)? {
        warn(
            &mut result,
            "binary64_gate_voltage_rounding_merges_distinct_inputs",
        );
    }
    let all_currents: Vec<&Rat> = points.iter().map(|p| &p.current).collect();
    if float_merges(&all_currents)? {
        warn(
            &mut result,
            "binary64_drain_current_rounding_merges_distinct_inputs",
        );
    }
    let currents: Vec<Rat> = selected_points.iter().map(|p| &p.current * sign).collect();
    if !(currents.windows(2).all(|w| w[1] >= w[0]) || currents.windows(2).all(|w| w[1] <= w[0])) {
        warn(
            &mut result,
            "nonmonotonic_selected_current_retained_not_trimmed",
        );
    }
    let fitted = match fit_points(&selected_points, sign) {
        Ok(fitted) => fitted,
        Err(reason) => {
            return Ok(reject(
                result,
                if reason == "log_precision_merges_distinct_currents"
                    || reason == "rank_deficient_gate_voltage"
                {
                    &reason
                } else {
                    "derived_numeric_range_error"
                },
            ));
        }
    };
    warn(
        &mut result,
        if fitted.sse.is_zero() {
            "zero_residuals_do_not_validate_subthreshold_regime"
        } else {
            "nonzero_residuals_may_include_log_rounding_noise_or_curvature"
        },
    );
    if fitted.syy.is_zero() {
        warn(&mut result, "constant_log_current_r_squared_undefined");
    }
    if fitted.slope.is_zero() {
        warn(&mut result, "zero_log_slope_reciprocal_undefined");
    }
    if fitted.slope.is_negative() {
        warn(&mut result, "negative_log_slope_sign_preserved");
    }
    let min_selected = selected_points.iter().map(|p| &p.vgs).min().unwrap();
    let max_selected = selected_points.iter().map(|p| &p.vgs).max().unwrap();
    if min_selected > &rat(0) || max_selected < &rat(0) {
        warn(
            &mut result,
            "zero_gate_intercept_is_extrapolated_diagnostic_only",
        );
    }
    let mut fit_index = 0;
    for (p, observation) in points
        .iter()
        .zip(result["observations"].as_array_mut().unwrap())
    {
        if selected(p, lower, upper) {
            observation
                .as_object_mut()
                .unwrap()
                .extend(fitted.fitted[fit_index].as_object().unwrap().clone());
            observation["used_in_fit"] = json!(true);
            fit_index += 1;
        }
    }
    result["status"] = json!("ok");
    result["fit"] = fitted.fit;
    result["reciprocal_slope"] = fitted.reciprocal;
    Ok(result)
}

pub fn run(a: &Args) -> Res<Value> {
    a.require_flag("accept-log-transfer-assumptions")?;
    let voltage_unit = a.choice("voltage-unit", VOLTAGE)?;
    let current_unit = a.choice("current-unit", CURRENT)?;
    let polarity = a.choice("current-polarity", &["positive", "negative"])?;
    let vf = unit(voltage_unit, "voltage")?;
    let cf = unit(current_unit, "current")?;
    let window_min = a.req("window-min")?;
    let window_max = a.req("window-max")?;
    let lower = number(window_min, &vf)?;
    let upper = number(window_max, &vf)?;
    if lower >= upper {
        return err("user voltage-window minimum must be strictly less than maximum");
    }
    let sign = rat(if polarity == "positive" { 1 } else { -1 });
    let (groups, rows, digest) = load(a, &vf, &cf)?;
    let mut results = vec![];
    for (key, points) in &groups {
        results.push(extract(key, points, &lower, &upper, &sign)?);
    }
    let mut summary = counts(&results, rows, groups.len());
    summary["distinct_physical_device_ids"] = json!(groups
        .keys()
        .map(|k| &k[..3])
        .collect::<BTreeSet<_>>()
        .len());
    let inside: u64 = results
        .iter()
        .map(|r| r["selected_sample_count"].as_u64().unwrap())
        .sum();
    summary["inside_window_rows"] = json!(inside);
    summary["outside_window_rows"] = json!(rows as u64 - inside);
    summary["fitted_rows"] = json!(results
        .iter()
        .filter(|r| r["status"] == "ok")
        .map(|r| r["selected_sample_count"].as_u64().unwrap())
        .sum::<u64>());
    Ok(
        json!({"schema_version":1,"tool":"transfer-log-slope","version":"0.1.0",
        "input_units":{"gate_drain_body_voltage":voltage_unit,"drain_current":current_unit,"temperature":"K"},
        "provenance":{"input_sha256":digest,"source_row_convention":"CSV physical line at end of record, header included","group_columns":GROUP,"references":constants("transfer-log-slope","SOURCES")},
        "parameters":{"log_transfer_assumptions_declared":true,"current_polarity":polarity,"current_normalization":"none",
            "window_min_input":py_trim(window_min),"window_max_input":py_trim(window_max),"window_min_V":finite(&lower)?,"window_max_V":finite(&upper)?,
            "window_inclusive":true,"minimum_selected_samples":3,"window_search":"not_performed","row_weighting":"equal","log_base":10,"log_reference_current_A":1,"log_decimal_precision":LOG_PRECISION},
        "counts":summary,"assumptions_and_limits":constants("transfer-log-slope","ASSUMPTIONS"),"results":results}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decimal_rounding_ties_to_even() {
        assert_eq!(
            round_significant(&decimal("1.25").unwrap(), 2),
            decimal("1.2").unwrap()
        );
        assert_eq!(
            round_significant(&decimal("1.35").unwrap(), 2),
            decimal("1.4").unwrap()
        );
        assert_eq!(
            round_significant(&decimal("-0.0125").unwrap(), 2),
            decimal("-0.012").unwrap()
        );
    }
    fn expected(text: &str) -> Rat {
        decimal_parts(text, 512, None, None, None, false).unwrap().0
    }
    #[test]
    fn logarithms_match_correctly_rounded_decimal80() {
        let pairs = [
            ("2","0.30102999566398119521373889472449302676818988146210854131042746112710818927442451"),
            ("3","0.47712125471966243729502790325511530920012886419069586482986564030522915278366112"),
            ("0.3","-0.52287874528033756270497209674488469079987113580930413517013435969477084721633888"),
            ("1.0000000000000000000000000000001","4.3429448190325182765112891891658336757030184321228400966850795435939820236942002e-32"),
        ];
        for (input, want) in pairs {
            assert_eq!(
                log10_fraction(&decimal(input).unwrap()).unwrap(),
                expected(want),
                "input={input}"
            );
        }
        for e in [-150, -1, 0, 1, 150] {
            assert_eq!(log10_fraction(&ten(e)).unwrap(), rat(e));
        }
    }
    #[test]
    fn near_equal_currents_remain_distinct() {
        let a = log10_fraction(&decimal("1").unwrap()).unwrap();
        let b = log10_fraction(&decimal("1.0000000000000000000000000000001").unwrap()).unwrap();
        let c = log10_fraction(&decimal("1.0000000000000000000000000000002").unwrap()).unwrap();
        assert!(a < b && b < c);
    }
    #[test]
    fn synthetic_golden_logs_match_all_80_digits() {
        let fixture: Value =
            serde_json::from_str(include_str!("../tests/transfer_log80_reference.json")).unwrap();
        for pair in fixture["values"].as_array().unwrap() {
            let input = pair[0].as_str().unwrap();
            let want = pair[1].as_str().unwrap();
            assert_eq!(
                log10_fraction(&decimal(input).unwrap()).unwrap(),
                expected(want),
                "input={input}"
            );
        }
    }
    #[test]
    fn reachable_numeric_range_failure_never_leaves_partial_fit() {
        let key: Vec<String> = ["l", "w", "d", "c", "r", "up"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        for middle in ["1e-150", "1e26"] {
            let points: Vec<Point> = ["-1e150", middle, "1e150"]
                .iter()
                .zip(["1", "1.0000000000000000000000000000001", "1"])
                .enumerate()
                .map(|(i, (gate, current))| Point {
                    source_row: i + 2,
                    temperature: rat(300),
                    vds: decimal("0.1").unwrap(),
                    vbs: rat(0),
                    vgs: decimal(gate).unwrap(),
                    current: decimal(current).unwrap(),
                    input_numeric_text: json!({}),
                })
                .collect();
            let result = extract(&key, &points, &-ten(150), &ten(150), &rat(1)).unwrap();
            assert_eq!(result["reason"], "derived_numeric_range_error");
            assert_eq!(result["fit"], Value::Null);
            assert_eq!(result["reciprocal_slope"], Value::Null);
            assert!(result["observations"]
                .as_array()
                .unwrap()
                .iter()
                .all(|o| o["used_in_fit"] == false && o.get("residual_decades").is_none()));
        }
    }
}
