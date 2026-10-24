//! Signed, descriptive Hall-response fits. No physical-model validation or offset removal.
use crate::common::*;
use std::collections::HashSet;

const GROUP_COLUMNS: &[&str] = &["lot", "wafer", "device", "condition", "replicate", "branch"];
const COLUMNS: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "replicate",
    "branch",
    "temperature_K",
    "bias_current",
    "magnetic_field",
    "transverse_voltage",
];

#[derive(Clone)]
struct Point {
    source_row: usize,
    temperature: Rat,
    current: Rat,
    field: Rat,
    voltage: Rat,
}

fn reject(mut result: Value, reason: &str) -> Value {
    result["reason"] = json!(reason);
    result
}

fn extract_group(key: &[String], points: &[Point]) -> Res<Value> {
    let distinct = points
        .iter()
        .map(|p| &p.field)
        .collect::<HashSet<_>>()
        .len();
    let mut result = identity(GROUP_COLUMNS, key);
    result["status"] = json!("rejected");
    result["sample_count"] = json!(points.len());
    result["distinct_field_count"] = json!(distinct);
    result["source_rows"] = json!(points.iter().map(|p| p.source_row).collect::<Vec<_>>());
    result["observations"] = Value::Array(
        points
            .iter()
            .map(|p| -> Res<Value> {
                Ok(
                    json!({"source_row": p.source_row, "temperature_K": finite(&p.temperature)?,
            "bias_current_A": finite(&p.current)?, "magnetic_field_T": finite(&p.field)?,
            "transverse_voltage_V": finite(&p.voltage)?}),
                )
            })
            .collect::<Res<_>>()?,
    );
    result["fit"] = Value::Null;
    result["current_normalized_sensitivity_V_per_A_T"] = Value::Null;
    let mut warnings = vec![
        "descriptive_fit_not_physical_model_validation",
        "branches_and_replicates_not_independent_devices",
        "offset_and_non_hall_contributions_not_removed",
    ];
    result["warnings"] = json!(warnings);
    if points.iter().any(|p| p.temperature <= Rat::zero()) {
        return Ok(reject(result, "nonpositive_temperature"));
    }
    if points
        .iter()
        .any(|p| p.temperature != points[0].temperature || p.current != points[0].current)
    {
        return Ok(reject(result, "inconsistent_temperature_or_bias_current"));
    }
    result["conditions"] = json!({"temperature_K": finite(&points[0].temperature)?, "bias_current_A": finite(&points[0].current)?});
    if distinct != points.len() {
        return Ok(reject(result, "duplicate_magnetic_field"));
    }
    if points.len() < 3 {
        return Ok(reject(result, "fewer_than_three_distinct_fields"));
    }
    let ascending = points.windows(2).all(|p| p[1].field > p[0].field);
    let descending = points.windows(2).all(|p| p[1].field < p[0].field);
    if !ascending && !descending {
        return Ok(reject(result, "nonmonotonic_magnetic_field"));
    }
    result["acquisition_direction"] = json!(if ascending {
        "increasing_field"
    } else {
        "decreasing_field"
    });
    for (values, warning) in [
        (
            points.iter().map(|p| &p.field).collect::<Vec<_>>(),
            "binary64_field_rounding_merges_distinct_inputs",
        ),
        (
            points.iter().map(|p| &p.voltage).collect::<Vec<_>>(),
            "binary64_voltage_rounding_merges_distinct_inputs",
        ),
    ] {
        let exact = values.iter().collect::<HashSet<_>>().len();
        let rounded = values
            .iter()
            .map(|p| finite(p).map(f64::to_bits))
            .collect::<Res<HashSet<_>>>()?
            .len();
        if exact != rounded {
            warnings.push(warning);
        }
    }
    let n = rat(points.len() as i64);
    let sx: Rat = points.iter().map(|p| &p.field).sum();
    let sy: Rat = points.iter().map(|p| &p.voltage).sum();
    let sxx: Rat = points.iter().map(|p| &p.field * &p.field).sum();
    let sxy: Rat = points.iter().map(|p| &p.field * &p.voltage).sum();
    let slope = (&n * sxy - &sx * &sy) / (&n * sxx - &sx * &sx);
    let intercept = (&sy - &slope * &sx) / &n;
    let predicted: Vec<Rat> = points
        .iter()
        .map(|p| &slope * &p.field + &intercept)
        .collect();
    let residuals: Vec<Rat> = points
        .iter()
        .zip(&predicted)
        .map(|(p, f)| &p.voltage - f)
        .collect();
    let sse: Rat = residuals.iter().map(|v| v * v).sum();
    let mean = sy / &n;
    let syy: Rat = points
        .iter()
        .map(|p| {
            let d = &p.voltage - &mean;
            &d * &d
        })
        .sum();
    let voltage_span = points.iter().map(|p| &p.voltage).max().unwrap()
        - points.iter().map(|p| &p.voltage).min().unwrap();
    let low = points.iter().map(|p| &p.field).min().unwrap();
    let high = points.iter().map(|p| &p.field).max().unwrap();
    let extrapolation = if low.is_positive() || high.is_negative() {
        points.iter().map(|p| p.field.abs()).min().unwrap() / (high - low)
    } else {
        Rat::zero()
    };
    if !extrapolation.is_zero() {
        warnings.push("zero_field_intercept_is_extrapolated");
    } else if !points.iter().any(|p| p.field.is_zero()) {
        warnings.push("zero_field_intercept_not_directly_sampled");
    }
    if extrapolation >= rat(10) {
        warnings.push("large_zero_field_extrapolation");
    }
    if !(low.is_negative() && high.is_positive()) {
        warnings.push("both_field_polarities_not_sampled");
    }
    if !sse.is_zero() {
        warnings.push("nonzero_residuals_possible_nonlinearity_noise_or_drift");
    } else {
        warnings.push("zero_residuals_do_not_validate_linearity");
    }
    if syy.is_zero() {
        warnings.push("constant_voltage_r_squared_undefined");
    }
    if slope.is_zero() {
        warnings.push("zero_slope_does_not_establish_absence_of_hall_effect");
    }
    let current = &points[0].current;
    if current.is_zero() {
        warnings.push("zero_bias_current_normalization_unavailable");
    }
    result["warnings"] = json!(warnings);
    let max_residual = residuals.iter().map(Signed::abs).max().unwrap();
    // Build derived values transactionally. No partial fit or fitted observations
    // are retained when normalization or another derived quantity is out of range.
    let derived: Res<(Value, Value, Vec<Value>)> = (|| {
        let fit = json!({
            "method": "unweighted_ordinary_least_squares", "slope_V_per_T": finite(&slope)?,
            "zero_field_intercept_V": finite(&intercept)?,
            "r_squared": if syy.is_zero() { None } else { Some(finite(&(Rat::one() - &sse / &syy))?) },
            "rmse_V": sqrt_rat(&(&sse / &n))?, "max_abs_residual_V": finite(&max_residual)?,
            "max_abs_residual_over_voltage_span": if voltage_span.is_zero() { None } else { Some(finite(&(&max_residual / &voltage_span))?) },
            "magnetic_field_range_T": [finite(low)?, finite(high)?],
            "zero_field_extrapolation_ratio": finite(&extrapolation)?
        });
        let normalized = if current.is_zero() {
            Value::Null
        } else {
            json!(finite(&(&slope / current))?)
        };
        let observations = result["observations"]
            .as_array()
            .unwrap()
            .iter()
            .zip(&predicted)
            .zip(&residuals)
            .map(|((raw, fitted), residual)| -> Res<Value> {
                let mut observation = raw.clone();
                observation["fitted_transverse_voltage_V"] = json!(finite(fitted)?);
                observation["residual_V"] = json!(finite(residual)?);
                Ok(observation)
            })
            .collect::<Res<Vec<_>>>()?;
        Ok((fit, normalized, observations))
    })();
    match derived {
        Ok((fit, normalized, observations)) => {
            result["status"] = json!("ok");
            result["fit"] = fit;
            result["observations"] = json!(observations);
            result["current_normalized_sensitivity_V_per_A_T"] = normalized;
            Ok(result)
        }
        Err(_) => Ok(reject(result, "derived_numeric_range_error")),
    }
}

pub fn run(a: &Args) -> Res<Value> {
    a.require_flag("accept-hall-assumptions")?;
    let field_unit = a.choice("field-unit", &["T", "mT", "uT", "G", "kG"])?;
    let voltage_unit = a.choice("voltage-unit", &["V", "mV", "uV", "nV"])?;
    let current_unit = a.choice("current-unit", &["A", "mA", "uA", "nA", "pA"])?;
    let ff = unit(field_unit, "field")?;
    let vf = unit(voltage_unit, "voltage")?;
    let cf = unit(current_unit, "current")?;
    let (rows, digest) = load_rows(&a.input, COLUMNS, true)?;
    let mut groups: IndexMap<Vec<String>, Vec<Point>> = IndexMap::new();
    for row in &rows {
        let key = group_key(row, GROUP_COLUMNS)?;
        let point = Point {
            source_row: row.line_end,
            temperature: number(row.get("temperature_K"), &Rat::one())?,
            current: number(row.get("bias_current"), &cf)?,
            field: number(row.get("magnetic_field"), &ff)?,
            voltage: number(row.get("transverse_voltage"), &vf)?,
        };
        let points = groups.entry(key).or_default();
        points.push(point);
        if points.len() > 1000 || groups.len() > 1000 {
            return err("CSV exceeds row or group limits");
        }
    }
    let results = groups
        .iter()
        .map(|(key, points)| extract_group(key, points))
        .collect::<Res<Vec<_>>>()?;
    let mut count = counts(&results, rows.len(), groups.len());
    count["distinct_physical_device_ids"] = json!(groups
        .keys()
        .map(|key| &key[..3])
        .collect::<HashSet<_>>()
        .len());
    Ok(
        json!({"schema_version": 1, "tool": "hall-response", "version": "0.1.0",
            "input_units": {"magnetic_field": field_unit, "transverse_voltage": voltage_unit, "bias_current": current_unit, "temperature": "K"},
            "provenance": {"input_sha256": digest, "source_row_convention": "CSV physical line at end of record, header included",
                "group_columns": GROUP_COLUMNS, "references": constants("hall-response", "SOURCES")},
            "parameters": {"hall_response_assumptions_declared": true, "minimum_distinct_fields": 3,
                "row_weighting": "equal", "opposite_field_pairing": "not_performed"},
            "counts": count, "assumptions_and_limits": constants("hall-response", "ASSUMPTIONS"), "results": results
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fit(x: &[&str], y: &[&str], current: &str) -> Value {
        let points = x
            .iter()
            .zip(y)
            .enumerate()
            .map(|(i, (x, y))| Point {
                source_row: i + 2,
                temperature: rat(300),
                current: decimal(current).unwrap(),
                field: decimal(x).unwrap(),
                voltage: decimal(y).unwrap(),
            })
            .collect::<Vec<_>>();
        extract_group(&["l", "w", "d", "c", "r", "up"].map(str::to_owned), &points).unwrap()
    }
    #[test]
    fn signed_normalization_and_zero_current() {
        let r = fit(
            &["-1", "0", "1"],
            &["-0.0017", "0.0003", "0.0023"],
            "-0.001",
        );
        assert_eq!(r["current_normalized_sensitivity_V_per_A_T"], -2.0);
        assert_eq!(r["fit"]["zero_field_intercept_V"], 0.0003);
        let z = fit(&["-1", "0", "1"], &["-0.0017", "0.0003", "0.0023"], "0");
        assert_eq!(z["status"], "ok");
        assert!(z["current_normalized_sensitivity_V_per_A_T"].is_null());
    }
    #[test]
    fn curvature_and_constant_response() {
        let r = fit(&["-1", "0", "1"], &["1", "0", "1"], "1");
        assert_eq!(r["fit"]["slope_V_per_T"], 0.0);
        assert_eq!(r["fit"]["r_squared"], 0.0);
        assert_eq!(r["fit"]["zero_field_intercept_V"], 2.0 / 3.0);
        let c = fit(&["-1", "0", "1"], &["3", "3", "3"], "1");
        assert!(c["fit"]["r_squared"].is_null());
        assert!(c["fit"]["max_abs_residual_over_voltage_span"].is_null());
    }
    #[test]
    fn duplicate_reversal_and_acquisition_order() {
        assert_eq!(
            fit(&["0", "-0.0", "1"], &["0", "1", "2"], "1")["reason"],
            "duplicate_magnetic_field"
        );
        assert_eq!(
            fit(&["0", "1", "-1"], &["0", "1", "2"], "1")["reason"],
            "nonmonotonic_magnetic_field"
        );
        let down = fit(&["1", "0", "-1"], &["2", "1", "0"], "1");
        assert_eq!(down["acquisition_direction"], "decreasing_field");
        assert_eq!(down["observations"][0]["magnetic_field_T"], 1.0);
    }
    #[test]
    fn exact_field_separation_and_warnings() {
        let r = fit(
            &[
                "1",
                "1.000000000000000000000000000001",
                "1.000000000000000000000000000002",
            ],
            &["0", "0.001", "0.002"],
            "1",
        );
        assert_eq!(r["status"], "ok");
        assert_eq!(r["fit"]["slope_V_per_T"], 1e27);
        assert!(r["warnings"]
            .as_array()
            .unwrap()
            .contains(&json!("binary64_field_rounding_merges_distinct_inputs")));
    }
    #[test]
    fn normalization_range_failure_is_atomic() {
        for (x, y, current) in [
            (
                ["-1e-150", "0", "1e-150"],
                ["-1e150", "0", "1e150"],
                "1e-150",
            ),
            (
                ["-1e150", "0", "1e150"],
                ["-1e-150", "0", "1e-150"],
                "1e150",
            ),
        ] {
            let r = fit(&x, &y, current);
            assert_eq!(r["reason"], "derived_numeric_range_error");
            assert!(r["fit"].is_null());
            assert!(r["current_normalized_sensitivity_V_per_A_T"].is_null());
            assert!(r["observations"][0].get("residual_V").is_none());
        }
    }
    #[test]
    fn tiny_residual_sqrt_not_underflowed() {
        let r = fit(&["-1", "0", "1"], &["1e-150", "0", "1e-150"], "1");
        assert_eq!(r["status"], "ok");
        let ratio = r["fit"]["rmse_V"].as_f64().unwrap() / 1e-150;
        assert!((ratio - (2.0_f64 / 9.0).sqrt()).abs() < 1e-15);
    }
}
