//! Exact, signed constant-current criterion extraction. No extrapolation or sign repair.
use crate::common::*;
use std::collections::{BTreeSet, HashSet};

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

#[derive(Clone, Debug)]
struct Point {
    source_row: usize,
    temperature: Rat,
    vds: Rat,
    vbs: Rat,
    vgs: Rat,
    current: Rat,
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
        };
        let points = groups.entry(key).or_default();
        points.push(point);
        if points.len() > 1000 || groups.len() > 1000 {
            return err("CSV exceeds row or group limits");
        }
    }
    Ok((groups, count, digest))
}

fn observation(p: &Point) -> Res<Value> {
    Ok(
        json!({"source_row":p.source_row, "gate_source_voltage_V":finite(&p.vgs)?,
        "drain_current_A":finite(&p.current)?, "drain_source_voltage_V":finite(&p.vds)?,
        "body_source_voltage_V":finite(&p.vbs)?, "temperature_K":finite(&p.temperature)?}),
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

fn extract(
    key: &[String],
    points: &[Point],
    criterion: &Rat,
    sign: &Rat,
    max_span: Option<&Rat>,
) -> Res<Value> {
    let mut result = identity(GROUP, key);
    result["status"] = json!("rejected");
    result["sample_count"] = json!(points.len());
    result["extraction"] = Value::Null;
    result["observations"] = json!(points.iter().map(observation).collect::<Res<Vec<_>>>()?);
    result["warnings"] = json!([
        "criterion_defined_value_not_universal_physical_threshold",
        "branches_and_replicates_not_independent_devices"
    ]);
    if points.len() < 2 {
        return Ok(reject(result, "fewer_than_two_samples"));
    }
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
    if points.iter().any(|p| (&p.current * sign).is_negative()) {
        return Ok(reject(
            result,
            "current_sign_conflicts_with_declared_polarity",
        ));
    }
    let voltages: Vec<&Rat> = points.iter().map(|p| &p.vgs).collect();
    let currents: Vec<&Rat> = points.iter().map(|p| &p.current).collect();
    if voltages.iter().copied().collect::<BTreeSet<_>>().len() != voltages.len() {
        return Ok(reject(result, "duplicate_gate_voltage"));
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
    result["gate_voltage_range_V"] = json!([
        finite(voltages.iter().min().unwrap())?,
        finite(voltages.iter().max().unwrap())?
    ]);
    result["drain_current_range_A"] = json!([
        finite(currents.iter().min().unwrap())?,
        finite(currents.iter().max().unwrap())?
    ]);
    if float_merges(&voltages)? {
        warn(
            &mut result,
            "binary64_gate_voltage_rounding_merges_distinct_inputs",
        );
    }
    if float_merges(&currents)? {
        warn(
            &mut result,
            "binary64_drain_current_rounding_merges_distinct_inputs",
        );
    }
    let exact: Vec<&Point> = points.iter().filter(|p| &p.current == criterion).collect();
    let interiors: Vec<&[Point]> = points
        .windows(2)
        .filter(|w| {
            (&w[0].current < criterion && criterion < &w[1].current)
                || (&w[1].current < criterion && criterion < &w[0].current)
        })
        .collect();
    if exact.len() + interiors.len() > 1 {
        return Ok(reject(result, "nonunique_current_crossing"));
    }
    if exact.is_empty() && interiors.is_empty() {
        return Ok(reject(result, "criterion_not_bracketed"));
    }
    let rising = currents.windows(2).all(|w| w[1] >= w[0]);
    let falling = currents.windows(2).all(|w| w[1] <= w[0]);
    if !(rising || falling) {
        return Ok(reject(result, "nonmonotonic_drain_current"));
    }
    result["current_direction"] = json!(if rising {
        "increasing_or_flat"
    } else {
        "decreasing_or_flat"
    });
    let source: Vec<&Point> = if !exact.is_empty() {
        vec![exact[0]]
    } else {
        interiors[0].iter().collect()
    };
    let is_exact = !exact.is_empty();
    let (threshold, weight, span, method) = if is_exact {
        (
            source[0].vgs.clone(),
            rat(0),
            rat(0),
            "measured_exact_criterion",
        )
    } else {
        let a = source[0];
        let b = source[1];
        let span = (&b.vgs - &a.vgs).abs();
        if max_span.is_some_and(|limit| &span > limit) {
            return Ok(reject(result, "bracket_span_exceeds_limit"));
        }
        let weight = (criterion - &a.current) / (&b.current - &a.current);
        let threshold = &a.vgs + &weight * (&b.vgs - &a.vgs);
        (threshold, weight, span, "linear_current_interpolation")
    };
    // Diagnostic weight alone may round to an endpoint, including zero. Threshold
    // representability is tested independently from the exact rational answer.
    let extraction = (|| -> Res<Value> {
        Ok(json!({"method":method,
            "gate_source_voltage_at_criterion_V":finite(&threshold)?,
            "signed_current_criterion_A":finite(criterion)?,
            "bracket_span_V":finite(&span)?,
            "fraction_from_first_to_second_sample":if is_exact {Value::Null} else {json!(weight.to_f64().ok_or("interpolation fraction range error")?)},
            "source_rows":source.iter().map(|p|p.source_row).collect::<Vec<_>>(),
            "bracket_gate_source_voltage_V":source.iter().map(|p|finite(&p.vgs)).collect::<Res<Vec<_>>>()?,
            "bracket_drain_current_A":source.iter().map(|p|finite(&p.current)).collect::<Res<Vec<_>>>()?}))
    })();
    let extraction = match extraction {
        Ok(v) => v,
        Err(_) => return Ok(reject(result, "derived_numeric_range_error")),
    };
    if !is_exact {
        warn(
            &mut result,
            "local_linear_interpolation_is_not_measurement_uncertainty",
        );
        let fraction = extraction["fraction_from_first_to_second_sample"]
            .as_f64()
            .unwrap();
        if fraction == 0.0 || fraction == 1.0 {
            warn(
                &mut result,
                "binary64_rounding_hides_interior_interpolation_fraction",
            );
        }
        let criterion_float = finite(criterion)?;
        if source
            .iter()
            .any(|p| finite(&p.current).ok() == Some(criterion_float))
        {
            warn(
                &mut result,
                "binary64_rounding_hides_criterion_bracket_separation",
            );
        }
    }
    result["status"] = json!("ok");
    result["extraction"] = extraction;
    Ok(result)
}

pub fn run(a: &Args) -> Res<Value> {
    a.require_flag("accept-transfer-assumptions")?;
    let voltage_unit = a.choice("voltage-unit", VOLTAGE)?;
    let current_unit = a.choice("current-unit", CURRENT)?;
    let criterion_unit = a.choice("criterion-current-unit", CURRENT)?;
    let polarity = a.choice("current-polarity", &["positive", "negative"])?;
    let vf = unit(voltage_unit, "voltage")?;
    let cf = unit(current_unit, "current")?;
    let criterion_input = a.req("criterion-current")?;
    let criterion = number(criterion_input, &unit(criterion_unit, "current")?)?;
    let sign = rat(if polarity == "positive" { 1 } else { -1 });
    if &criterion * &sign <= rat(0) {
        return err("current criterion must be nonzero and match the declared current polarity");
    }
    let span = a
        .opt("max-bracket-span")
        .map(|s| number(s, &vf))
        .transpose()?;
    if span.as_ref().is_some_and(|s| s <= &rat(0)) {
        return err("maximum bracket span must be positive");
    }
    let branch = a
        .opt("branch")
        .map(|s| identifier(s, 128, false))
        .transpose()?;
    let (groups, row_count, digest) = load(a, &vf, &cf)?;
    let mut results = vec![];
    for (key, points) in &groups {
        if branch.as_ref().is_some_and(|b| key.last().unwrap() != b) {
            continue;
        }
        results.push(extract(key, points, &criterion, &sign, span.as_ref())?);
    }
    if results.is_empty() {
        return err("branch filter matched no measurement group");
    }
    let mut summary = counts(&results, row_count, groups.len());
    summary["selected_groups"] = json!(results.len());
    summary["excluded_by_branch_filter"] = json!(groups.len() - results.len());
    Ok(
        json!({"schema_version":1, "tool":"vth-constant-current", "version":"0.1.0",
        "input_units":{"gate_drain_body_voltage":voltage_unit,"drain_current":current_unit,"temperature":"K","criterion_current":criterion_unit},
        "provenance":{"input_sha256":digest,"source_row_convention":"CSV physical line at end of record, header included","group_columns":GROUP,"reference":constants("vth-constant-current","SOURCE_URL")},
        "parameters":{"transfer_assumptions_declared":true,"current_polarity":polarity,"criterion_current_input":py_trim(criterion_input),"signed_current_criterion_A":finite(&criterion)?,"current_normalization":"none","interpolation":"linear_current","branch_filter":branch,"max_bracket_span_input":a.opt("max-bracket-span").map(py_trim),"max_bracket_span_V":span.as_ref().map(finite).transpose()?},
        "counts":summary,"assumptions_and_limits":constants("vth-constant-current","ASSUMPTIONS"),"results":results}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn point(v: &str, current: &str, row: usize) -> Point {
        Point {
            source_row: row,
            temperature: rat(300),
            vds: decimal("0.1").unwrap(),
            vbs: rat(0),
            vgs: decimal(v).unwrap(),
            current: decimal(current).unwrap(),
        }
    }
    fn key() -> Vec<String> {
        ["l", "w", "d", "c", "r", "up"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }
    #[test]
    fn exact_fraction_survives_diagnostic_underflow() {
        let points = vec![point("0", "1e-150", 2), point("1e150", "1e150", 3)];
        let result = extract(
            &key(),
            &points,
            &decimal("1.0000000000000000000000000000001e-150").unwrap(),
            &rat(1),
            None,
        )
        .unwrap();
        assert_eq!(result["status"], "ok");
        assert_eq!(
            result["extraction"]["gate_source_voltage_at_criterion_V"],
            1e-181
        );
        assert_eq!(
            result["extraction"]["fraction_from_first_to_second_sample"],
            0.0
        );
        let reverse: Vec<_> = points.into_iter().rev().collect();
        let result = extract(
            &key(),
            &reverse,
            &decimal("1.0000000000000000000000000000001e-150").unwrap(),
            &rat(1),
            None,
        )
        .unwrap();
        assert_eq!(
            result["extraction"]["gate_source_voltage_at_criterion_V"],
            1e-181
        );
        assert_eq!(
            result["extraction"]["fraction_from_first_to_second_sample"],
            1.0
        );
    }
    #[test]
    fn nonunique_crossing_precedes_current_monotonicity() {
        let points = vec![point("0", "0", 2), point("1", "2", 3), point("2", "0", 4)];
        assert_eq!(
            extract(&key(), &points, &rat(1), &rat(1), None).unwrap()["reason"],
            "nonunique_current_crossing"
        );
    }
    #[test]
    fn exact_hit_ignores_neighbor_span() {
        let points = vec![point("0", "0", 2), point("1", "1", 3), point("100", "2", 4)];
        let result = extract(
            &key(),
            &points,
            &rat(1),
            &rat(1),
            Some(&decimal("0.001").unwrap()),
        )
        .unwrap();
        assert_eq!(result["status"], "ok");
        assert_eq!(result["extraction"]["source_rows"], json!([3]));
    }
}
