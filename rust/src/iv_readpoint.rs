use crate::common::*;
use std::collections::HashSet;
const GROUP: &[&str] = &["device", "cycle", "branch"];
const COLUMNS: &[&str] = &["device", "cycle", "branch", "voltage", "current"];
#[derive(Clone)]
pub struct Point {
    pub v: f64,
    pub i: f64,
    pub row: usize,
}
pub fn extract_group(
    key: &[String],
    points: &[Point],
    target: f64,
    max_span: Option<f64>,
) -> Value {
    let mut r = identity(GROUP, key);
    r["sample_count"] = json!(points.len());
    r["status"] = json!("rejected");
    let reject = |mut r: Value, reason: &str| {
        r["reason"] = json!(reason);
        r
    };
    if points
        .iter()
        .map(|p| if p.v == 0.0 { 0 } else { p.v.to_bits() })
        .collect::<HashSet<_>>()
        .len()
        != points.len()
    {
        return reject(r, "duplicate_voltage");
    }
    let ascending = points.windows(2).all(|w| w[1].v > w[0].v);
    let descending = points.windows(2).all(|w| w[1].v < w[0].v);
    if !ascending && !descending {
        return reject(r, "nonmonotonic_voltage");
    }
    r["acquisition_direction"] = json!(if points.len() == 1 {
        "single_point"
    } else if ascending {
        "increasing"
    } else {
        "decreasing"
    });
    let lo = points.iter().map(|p| p.v).fold(f64::INFINITY, f64::min);
    let hi = points.iter().map(|p| p.v).fold(f64::NEG_INFINITY, f64::max);
    r["voltage_range_V"] = json!([lo, hi]);
    if target < lo || target > hi {
        return reject(r, "read_voltage_outside_branch");
    }
    let source: Vec<&Point>;
    let current;
    let method;
    let span;
    if let Some(p) = points.iter().find(|p| p.v == target) {
        source = vec![p];
        current = p.i;
        method = "measured";
        span = 0.0;
    } else {
        let ordered: Vec<&Point> = if ascending {
            points.iter().collect()
        } else {
            points.iter().rev().collect()
        };
        let Some(pair) = ordered
            .windows(2)
            .find(|w| w[0].v < target && target < w[1].v)
        else {
            return reject(r, "no_adjacent_bracket");
        };
        source = pair.to_vec();
        let delta = f64rat(source[1].v) - f64rat(source[0].v);
        if max_span.is_some_and(|m| delta > f64rat(m)) {
            return reject(r, "bracket_span_exceeds_limit");
        };
        let weight = (f64rat(target) - f64rat(source[0].v)) / &delta;
        let exact = f64rat(source[0].i) + weight * (f64rat(source[1].i) - f64rat(source[0].i));
        match (finite(&exact), finite(&delta)) {
            (Ok(c), Ok(s)) => {
                current = c;
                span = s
            }
            _ => return reject(r, "derived_numeric_range_error"),
        };
        method = "linear_interpolation";
    }
    let conductance = match finite(&(f64rat(current) / f64rat(target))) {
        Ok(c) => c,
        Err(_) => return reject(r, "derived_numeric_range_error"),
    };
    r["status"] = json!("ok");
    r["method"] = json!(method);
    r["read_voltage_V"] = json!(target);
    r["current_A"] = json!(current);
    r["conductance_S"] = json!(conductance);
    r["bracket_span_V"] = json!(span);
    r["source_rows"] = json!(source.iter().map(|p| p.row).collect::<Vec<_>>());
    r["bracket_voltage_V"] = json!(source.iter().map(|p| p.v).collect::<Vec<_>>());
    r["bracket_current_A"] = json!(source.iter().map(|p| p.i).collect::<Vec<_>>());
    r
}
pub fn run(a: &Args) -> Res<Value> {
    let vu = a.choice("voltage-unit", &["V", "mV", "uV"])?;
    let cu = a.choice("current-unit", &["A", "mA", "uA", "nA", "pA"])?;
    let ru = a.choice("read-voltage-unit", &["V", "mV", "uV"])?;
    let target = number_float(a.req("read-voltage")?, &unit(ru, "voltage")?)?;
    if target == 0.0 {
        return err("read voltage must be nonzero to calculate static conductance");
    };
    let max_span = a
        .opt("max-bracket-span")
        .map(|s| number_float(s, &unit(ru, "voltage")?))
        .transpose()?;
    if max_span.is_some_and(|s| s <= 0.0) {
        return err("maximum bracket span must be positive");
    };
    let (groups, row_count) = load_csv(&a.input, vu, cu)?;
    let results: Vec<Value> = groups
        .iter()
        .filter(|(k, _)| a.opt("branch").is_none_or(|b| b == k[2]))
        .map(|(key, p)| extract_group(key, p, target, max_span))
        .collect();
    if results.is_empty() {
        return err("branch filter matched no measurement group");
    };
    let mut count = counts(&results, row_count, groups.len());
    count["selected_groups"] = json!(results.len());
    count["excluded_by_branch_filter"] = json!(groups.len() - results.len());
    Ok(
        json!({"schema_version":1,"tool":"iv-readpoint","version":"0.1.0","input_units":{"voltage":vu,"current":cu},"parameters":{"read_voltage_V":target,"max_bracket_span_V":max_span,"branch_filter":a.opt("branch")},"counts":count,"assumptions_and_limits":constants("iv-readpoint","ASSUMPTIONS"),"results":results}),
    )
}

/// Native workflow adapter: one parse, same core input normalization and source lines.
pub fn load_csv(
    path: &std::path::Path,
    vu: &str,
    cu: &str,
) -> Res<(IndexMap<Vec<String>, Vec<Point>>, usize)> {
    if !["V", "mV", "uV"].contains(&vu) || !["A", "mA", "uA", "nA", "pA"].contains(&cu) {
        return err("unsupported input unit");
    }
    let (rows, _) = load_rows_bounded(path, COLUMNS, true, usize::MAX - 1, usize::MAX)?;
    let mut groups: IndexMap<Vec<String>, Vec<Point>> = IndexMap::new();
    for row in &rows {
        let key = GROUP
            .iter()
            .map(|c| identifier(row.get(c), 256, false))
            .collect::<Res<Vec<_>>>()?;
        let p = Point {
            v: number_float(row.get("voltage"), &unit(vu, "voltage")?)?,
            i: number_float(row.get("current"), &unit(cu, "current")?)?,
            row: row.line_end,
        };
        groups.entry(key).or_default().push(p);
    }
    Ok((groups, rows.len()))
}
