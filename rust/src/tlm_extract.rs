//! Conventional linear TLM, with exact regression and declared physical assumptions.
//! No values are clipped, no rows are dropped, and no group is silently pooled.
use crate::common::*;
use std::collections::{HashMap, HashSet};

const GROUP_COLUMNS: &[&str] = &["lot", "wafer", "structure", "condition", "replicate"];
const COLUMNS: &[&str] = &[
    "lot",
    "wafer",
    "structure",
    "condition",
    "replicate",
    "pair",
    "spacing",
    "width",
    "resistance",
];

#[derive(Clone)]
struct Point {
    pair: String,
    source_row: usize,
    spacing: Rat,
    width: Rat,
    resistance: Rat,
}

fn reject(mut result: Value, reason: &str) -> Value {
    result["reason"] = json!(reason);
    result
}

fn extract_group(key: &[String], points: &[Point]) -> Res<Value> {
    let distinct = points
        .iter()
        .map(|p| &p.spacing)
        .collect::<HashSet<_>>()
        .len();
    let mut result = identity(GROUP_COLUMNS, key);
    result["status"] = json!("rejected");
    result["sample_count"] = json!(points.len());
    result["distinct_spacing_count"] = json!(distinct);
    result["source_rows"] = json!(points.iter().map(|p| p.source_row).collect::<Vec<_>>());
    result["fit"] = Value::Null;
    result["physical_estimates"] = Value::Null;
    result["warnings"] = json!([]);
    // Observations are normalized and retained even for groups rejected before fitting.
    result["observations"] = Value::Array(
        points
            .iter()
            .map(|p| -> Res<Value> {
                Ok(json!({"pair": p.pair, "source_row": p.source_row,
            "spacing_m": finite(&p.spacing)?, "width_m": finite(&p.width)?,
            "resistance_ohm": finite(&p.resistance)?}))
            })
            .collect::<Res<_>>()?,
    );
    if points.iter().map(|p| &p.pair).collect::<HashSet<_>>().len() != points.len() {
        return Ok(reject(result, "duplicate_pair_id"));
    }
    if points
        .iter()
        .any(|p| p.spacing <= Rat::zero() || p.width <= Rat::zero())
    {
        return Ok(reject(result, "nonpositive_geometry"));
    }
    if points.iter().any(|p| p.resistance <= Rat::zero()) {
        return Ok(reject(result, "nonpositive_measured_resistance"));
    }
    if points.iter().any(|p| p.width != points[0].width) {
        return Ok(reject(result, "inconsistent_width"));
    }
    if distinct < 3 {
        return Ok(reject(result, "fewer_than_three_distinct_spacings"));
    }
    let mut warnings = vec![
        "zero_spacing_intercept_is_extrapolated",
        "rows_not_assumed_independent",
    ];
    if distinct != points.len() {
        warnings.push("repeated_spacing_equal_row_weighting");
    }
    let rounded_distinct = points
        .iter()
        .map(|p| finite(&p.spacing).map(f64::to_bits))
        .collect::<Res<HashSet<_>>>()?
        .len();
    if rounded_distinct < distinct {
        warnings.push("binary64_spacing_rounding_merges_distinct_inputs");
    }
    let n = rat(points.len() as i64);
    let sx: Rat = points.iter().map(|p| &p.spacing).sum();
    let sy: Rat = points.iter().map(|p| &p.resistance).sum();
    let sxx: Rat = points.iter().map(|p| &p.spacing * &p.spacing).sum();
    let sxy: Rat = points.iter().map(|p| &p.spacing * &p.resistance).sum();
    let slope = (&n * sxy - &sx * &sy) / (&n * sxx - &sx * &sx);
    let intercept = (&sy - &slope * &sx) / &n;
    let predicted: Vec<Rat> = points
        .iter()
        .map(|p| &slope * &p.spacing + &intercept)
        .collect();
    let residuals: Vec<Rat> = points
        .iter()
        .zip(&predicted)
        .map(|(p, f)| &p.resistance - f)
        .collect();
    let sse: Rat = residuals.iter().map(|v| v * v).sum();
    let mean = sy / &n;
    let syy: Rat = points
        .iter()
        .map(|p| {
            let d = &p.resistance - &mean;
            &d * &d
        })
        .sum();
    let low = points.iter().map(|p| &p.spacing).min().unwrap();
    let high = points.iter().map(|p| &p.spacing).max().unwrap();
    let extrapolation = low / (high - low);
    if extrapolation >= rat(10) {
        warnings.push("large_zero_spacing_extrapolation");
    }
    if syy.is_zero() {
        warnings.push("constant_resistance_r_squared_undefined");
    }
    if intercept.is_zero() {
        warnings.push("zero_intercept_not_proof_of_zero_contact_resistance");
    }
    result["warnings"] = json!(warnings);
    let max_residual = residuals.iter().map(Signed::abs).max().unwrap();
    // Preserve Python's staged assignment: a completed fit and any completed
    // fitted observations remain visible if a later derived conversion fails.
    let conversion: Res<()> = (|| {
        result["fit"] = json!({
            "method": "unweighted_ordinary_least_squares",
            "slope_ohm_per_m": finite(&slope)?,
            "intercept_ohm": finite(&intercept)?,
            "r_squared": if syy.is_zero() { None } else { Some(finite(&(Rat::one() - &sse / &syy))?) },
            "rmse_ohm": sqrt_rat(&(&sse / &n))?,
            "max_abs_residual_ohm": finite(&max_residual)?,
            "spacing_range_m": [finite(low)?, finite(high)?],
            "width_m": finite(&points[0].width)?,
            "zero_spacing_extrapolation_ratio": finite(&extrapolation)?
        });
        for ((observation, fitted), residual) in result["observations"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .zip(&predicted)
            .zip(&residuals)
        {
            observation["fitted_resistance_ohm"] = json!(finite(fitted)?);
            observation["residual_ohm"] = json!(finite(residual)?);
        }
        let mut issues = vec![];
        if slope <= Rat::zero() {
            issues.push("nonpositive_slope");
        }
        if intercept.is_negative() {
            issues.push("negative_intercept");
        }
        if !issues.is_empty() {
            result["reason"] = json!("nonphysical_fit");
            result["fit_failures"] = json!(issues);
            return Ok(());
        }
        result["physical_estimates"] = json!({
            "sheet_resistance_ohm_per_square": finite(&(&slope * &points[0].width))?,
            "per_contact_resistance_ohm": finite(&(&intercept / rat(2)))?
        });
        result["status"] = json!("ok");
        Ok(())
    })();
    if conversion.is_err() {
        result["reason"] = json!("derived_numeric_range_error");
    }
    Ok(result)
}

pub fn run(a: &Args) -> Res<Value> {
    a.require_flag("accept-linear-tlm-assumptions")?;
    let length_unit = a.choice("length-unit", &["m", "mm", "um", "nm"])?;
    let resistance_unit = a.choice("resistance-unit", &["ohm", "kohm", "Mohm"])?;
    let lf = unit(length_unit, "length")?;
    let rf = unit(resistance_unit, "resistance")?;
    let (rows, digest) = load_rows(&a.input, COLUMNS, true)?;
    let mut groups: IndexMap<Vec<String>, Vec<Point>> = IndexMap::new();
    for row in &rows {
        let key = group_key(row, GROUP_COLUMNS)?;
        let point = Point {
            pair: identifier(row.get("pair"), 128, false)?,
            source_row: row.line_end,
            spacing: number(row.get("spacing"), &lf)?,
            width: number(row.get("width"), &lf)?,
            resistance: number(row.get("resistance"), &rf)?,
        };
        let points = groups.entry(key).or_default();
        points.push(point);
        if points.len() > 1000 || groups.len() > 1000 {
            return err("CSV exceeds row or group limits");
        }
    }
    let mut results = groups
        .iter()
        .map(|(key, points)| extract_group(key, points))
        .collect::<Res<Vec<_>>>()?;
    let mut replicates: HashMap<&[String], HashSet<&String>> = HashMap::new();
    for key in groups.keys() {
        replicates.entry(&key[..4]).or_default().insert(&key[4]);
    }
    for (key, result) in groups.keys().zip(&mut results) {
        if replicates[&key[..4]].len() > 1 {
            result["warnings"]
                .as_array_mut()
                .unwrap()
                .push(json!("replicate_groups_not_independent_devices"));
        }
    }
    Ok(json!({
        "schema_version": 1, "tool": "tlm-extract", "version": "0.1.0",
        "input_units": {"spacing_and_width": length_unit, "resistance": resistance_unit},
        "provenance": {"input_sha256": digest,
            "source_row_convention": "CSV physical line at end of record, header included",
            "group_columns": GROUP_COLUMNS, "reference": constants("tlm-extract", "SOURCE_URL")},
        "parameters": {"linear_tlm_assumptions_declared": true, "minimum_distinct_spacings": 3, "row_weighting": "equal"},
        "counts": counts(&results, rows.len(), groups.len()),
        "assumptions_and_limits": constants("tlm-extract", "ASSUMPTIONS"), "results": results
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fit(x: &[&str], y: &[&str], width: &str) -> Value {
        let points: Vec<Point> = x
            .iter()
            .zip(y)
            .enumerate()
            .map(|(i, (x, y))| Point {
                pair: format!("p{i}"),
                source_row: i + 2,
                spacing: decimal(x).unwrap(),
                width: decimal(width).unwrap(),
                resistance: decimal(y).unwrap(),
            })
            .collect();
        extract_group(&["l", "w", "s", "c", "r"].map(str::to_owned), &points).unwrap()
    }
    #[test]
    fn exact_line_and_per_contact() {
        let r = fit(
            &["2e-6", "5e-6", "1e-5", "2e-5"],
            &["30", "60", "110", "210"],
            "1e-5",
        );
        assert_eq!(r["status"], "ok");
        assert_eq!(
            r["physical_estimates"],
            json!({"sheet_resistance_ohm_per_square": 100.0, "per_contact_resistance_ohm": 5.0})
        );
        assert_eq!(r["fit"]["rmse_ohm"], 0.0);
    }
    #[test]
    fn noisy_exact_residuals() {
        let r = fit(&["1", "2", "3"], &["4", "4", "10"], "2");
        assert_eq!(r["fit"]["r_squared"], 0.75);
        assert_eq!(r["fit"]["intercept_ohm"], 0.0);
        assert_eq!(r["fit"]["rmse_ohm"], 2.0_f64.sqrt());
        let residuals: Vec<_> = r["observations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p["residual_ohm"].clone())
            .collect();
        assert_eq!(residuals, vec![json!(1.0), json!(-2.0), json!(1.0)]);
    }
    #[test]
    fn exact_nearby_spacing_not_merged() {
        let r = fit(
            &["1", "1.00000000000000000001", "1.00000000000000000002"],
            &["3", "3.00000000000000000002", "3.00000000000000000004"],
            "1",
        );
        assert_eq!(r["fit"]["slope_ohm_per_m"], 2.0);
        assert_eq!(r["distinct_spacing_count"], 3);
        assert!(r["warnings"]
            .as_array()
            .unwrap()
            .contains(&json!("binary64_spacing_rounding_merges_distinct_inputs")));
    }
    #[test]
    fn negative_intercept_is_retained_and_rejected() {
        let r = fit(&["1", "2", "3"], &["1", "3", "5"], "1");
        assert_eq!(r["reason"], "nonphysical_fit");
        assert_eq!(r["fit"]["intercept_ohm"], -1.0);
        assert_eq!(r["fit_failures"], json!(["negative_intercept"]));
        assert!(r["physical_estimates"].is_null());
    }
    #[test]
    fn range_error_retains_fit() {
        let r = fit(
            &["1e-150", "2e-150", "3e-150"],
            &["2e149", "3e149", "4e149"],
            "1e150",
        );
        assert_eq!(r["reason"], "derived_numeric_range_error");
        assert!(!r["fit"].is_null());
        assert!(r["physical_estimates"].is_null());
        assert!(r["observations"][0].get("residual_ohm").is_some());
    }
    #[test]
    fn tiny_rmse_survives_squared_underflow() {
        let r = fit(
            &["1", "2", "3"],
            &["1.000000000000000000000000000001e-150", "2e-150", "3e-150"],
            "1",
        );
        assert!(r["fit"]["rmse_ohm"].as_f64().unwrap() > 0.0);
    }
}
