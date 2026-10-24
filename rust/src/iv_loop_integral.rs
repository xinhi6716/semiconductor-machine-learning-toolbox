use crate::common::*;
use std::collections::HashSet;
const GROUP: &[&str] = &["lot", "wafer", "device", "condition", "cycle", "replicate"];
const COLUMNS: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "cycle",
    "replicate",
    "sample_id",
    "branch_to_next",
    "voltage",
    "current",
];
#[derive(Clone)]
struct Point {
    row: Row,
    v: Rat,
    i: Rat,
}
fn bounded(v: Rat) -> Res<Rat> {
    if v.numer().bits() > 4096 || v.denom().bits() > 4096 {
        err("numeric resource limit prevents a complete exact audit")
    } else {
        Ok(v)
    }
}
pub fn exact_decimal(v: &Rat) -> Res<String> {
    bounded(v.clone())?;
    if v.is_zero() {
        return Ok("0".into());
    }
    let mut denominator = v.denom().clone();
    let (mut twos, mut fives) = (0u32, 0u32);
    while &denominator % 2u32 == BigInt::zero() {
        denominator /= 2u32;
        twos += 1
    }
    while &denominator % 5u32 == BigInt::zero() {
        denominator /= 5u32;
        fives += 1
    }
    if denominator != BigInt::one() {
        return err("exact audit requires terminating decimal arithmetic");
    };
    let places = twos.max(fives);
    let mut coefficient =
        v.numer().abs() * BigInt::from(2).pow(places - twos) * BigInt::from(5).pow(places - fives);
    let mut exponent = -(places as i64);
    while &coefficient % 10u32 == BigInt::zero() {
        coefficient /= 10u32;
        exponent += 1
    }
    let digits = coefficient.to_string();
    exponent += digits.len() as i64 - 1;
    let mut result = if v.is_negative() {
        "-".to_string()
    } else {
        String::new()
    };
    result.push_str(&digits[..1]);
    if digits.len() > 1 {
        result.push('.');
        result.push_str(&digits[1..]);
    }
    result.push_str(&format!("e{:+}", exponent));
    if result.len() > 1024 {
        return err("numeric resource limit prevents a complete exact audit");
    };
    Ok(result)
}
fn audit(key: &[String], points: &[Point]) -> Res<Value> {
    let mut observations = vec![];
    for (i, p) in points.iter().enumerate() {
        observations.push(json!({"sample_id":p.row.get("sample_id"),"branch_to_next":p.row.get("branch_to_next"),"voltage_text":p.row.get("voltage"),"current_text":p.row.get("current"),"source_record_index":p.row.ordinal,"source_line_start":p.row.line_start,"source_line_end":p.row.line_end,"sample_index":i+1,"voltage_V_exact":exact_decimal(&p.v)?,"current_A_exact":exact_decimal(&p.i)?,"branch_block_indices":[]}));
    }
    let mut edges = vec![];
    let mut branches: Vec<Value> = vec![];
    let mut sums: Vec<Rat> = vec![];
    let mut signs: Vec<HashSet<&str>> = vec![];
    let mut total = Rat::zero();
    for (i, w) in points.windows(2).enumerate() {
        let edge = i + 1;
        let delta = bounded(&w[1].v - &w[0].v)?;
        let mean = bounded(bounded(&w[0].i + &w[1].i)? / rat(2))?;
        let term = bounded(mean * &delta)?;
        let direction = if delta.is_positive() {
            "increasing"
        } else if delta.is_negative() {
            "decreasing"
        } else {
            "flat"
        };
        let label = w[0].row.get("branch_to_next");
        if branches.last().is_none_or(|b| b["branch_id"] != label) {
            branches.push(json!({"branch_block_index":branches.len()+1,"branch_id":label,"edge_start":edge,"edge_end":edge,"flat_edge_count":0}));
            sums.push(Rat::zero());
            signs.push(HashSet::new());
        }
        let block = branches.last_mut().unwrap();
        block["edge_end"] = json!(edge);
        if delta.is_zero() {
            block["flat_edge_count"] = json!(block["flat_edge_count"].as_u64().unwrap() + 1)
        } else {
            signs.last_mut().unwrap().insert(direction);
        }
        let last = sums.last_mut().unwrap();
        *last = bounded(&*last + &term)?;
        total = bounded(total + &term)?;
        edges.push(json!({"edge_index":edge,"left_sample_index":edge,"right_sample_index":edge+1,"branch_block_index":block["branch_block_index"],"branch_id":label,"direction":direction,"delta_voltage_V_exact":exact_decimal(&delta)?,"trapezoid_A_V_exact":exact_decimal(&term)?}));
    }
    for ((b, value), directions) in branches.iter_mut().zip(&sums).zip(&signs) {
        let start = b["edge_start"].as_u64().unwrap() as usize;
        let end = b["edge_end"].as_u64().unwrap() as usize;
        b["edge_count"] = json!(end - start + 1);
        b["sample_indices"] = json!((start..=end + 1).collect::<Vec<_>>());
        b["direction"] = json!(if directions.len() == 2 {
            "mixed"
        } else {
            directions.iter().next().copied().unwrap_or("flat")
        });
        b["signed_path_integral_A_V_exact"] = json!(exact_decimal(value)?);
        b["signed_path_integral_A_V"] = Value::Null;
        for i in start..=end + 1 {
            observations[i - 1]["branch_block_indices"]
                .as_array_mut()
                .unwrap()
                .push(b["branch_block_index"].clone());
        }
    }
    let shared:Vec<Value>=observations.iter().filter(|p|p["branch_block_indices"].as_array().unwrap().len()==2).map(|p|json!({"sample_index":p["sample_index"],"sample_id":p["sample_id"],"branch_block_indices":p["branch_block_indices"]})).collect();
    let vd = bounded(&points.last().unwrap().v - &points[0].v)?;
    let id = bounded(&points.last().unwrap().i - &points[0].i)?;
    let span = bounded(
        points.iter().map(|p| &p.v).max().unwrap() - points.iter().map(|p| &p.v).min().unwrap(),
    )?;
    let mut reasons = vec![];
    if points.len() < 3 {
        reasons.push("insufficient_samples")
    }
    if span.is_zero() {
        reasons.push("zero_voltage_span")
    }
    if !vd.is_zero() {
        reasons.push("voltage_endpoints_not_equal")
    }
    if branches
        .iter()
        .map(|b| b["branch_id"].as_str().unwrap())
        .collect::<HashSet<_>>()
        .len()
        != branches.len()
    {
        reasons.push("noncontiguous_declared_branch")
    }
    if signs.iter().any(|s| s.len() == 2) {
        reasons.push("reversing_declared_branch")
    }
    let mut metrics = Value::Null;
    if reasons.is_empty() {
        let summary = finite(&total);
        let branch_floats: Res<Vec<f64>> = sums.iter().map(finite).collect();
        if let (Ok(v), Ok(bs)) = (summary, branch_floats) {
            metrics = json!({"signed_path_integral_A_V":v,"signed_path_integral_A_V_exact":exact_decimal(&total)?});
            for (b, v) in branches.iter_mut().zip(bs) {
                b["signed_path_integral_A_V"] = json!(v);
            }
        } else {
            reasons.push("derived_numeric_range_error")
        }
    }
    let mut warnings = constants("iv-loop-integral", "BASE_WARNINGS")
        .as_array()
        .unwrap()
        .clone();
    if edges.iter().any(|e| e["direction"] == "flat") {
        warnings.push(json!("flat_edges_retained"))
    }
    if !shared.is_empty() {
        warnings.push(json!("branch_endpoints_shared_observations"))
    }
    if vd.is_zero() && !id.is_zero() {
        warnings.push(json!("voltage_closed_current_endpoints_differ"))
    }
    if !metrics.is_null() && total.is_zero() {
        warnings.push(json!("zero_signed_integral_not_absence_of_hysteresis"));
        if edges.iter().any(|e| e["trapezoid_A_V_exact"] != "0") {
            warnings.push(json!("nonzero_edge_contributions_cancel_exactly"))
        }
    }
    Ok(
        json!({"key":identity(GROUP,key),"status":if reasons.is_empty(){"usable"}else{"rejected"},"rejection_reasons":reasons,"primary_rejection_reason":reasons.first(),"sample_count":points.len(),"edge_count":edges.len(),"observations":observations,"edges":edges,"branch_audits":branches,"shared_endpoints":shared,"closure":{"voltage_endpoint_difference_V_exact":exact_decimal(&vd)?,"current_endpoint_difference_A_exact":exact_decimal(&id)?,"voltage_closed_exact":vd.is_zero(),"current_endpoints_equal_exact":id.is_zero(),"iv_endpoint_closed_exact":vd.is_zero()&&id.is_zero(),"added_closure_edge":false},"voltage_span_V_exact":exact_decimal(&span)?,"metrics":metrics,"feature_availability":{"earliest_required_sample_index":points.len(),"earliest_required_sample_id":points.last().unwrap().row.get("sample_id"),"time_verified":false,"prediction_eligibility":"not_established","required_boundary":"complete_feature_available_at_or_before_prediction_origin_and_target_strictly_after_origin"},"warnings":warnings}),
    )
}
pub fn run(a: &Args) -> Res<Value> {
    a.require_flag("accept-supplied-path-assumptions")?;
    let vu = a.choice("voltage-unit", &["V", "mV", "uV", "nV"])?;
    let cu = a.choice("current-unit", &["A", "mA", "uA", "nA", "pA"])?;
    let vf = unit(vu, "voltage")?;
    let cf = unit(cu, "current")?;
    let raw = read_regular(&a.input, MAX_BYTES)?;
    let (rows, digest) = parse_rows(&raw, COLUMNS, false, 10000)?;
    let mut groups: IndexMap<Vec<String>, Vec<Point>> = IndexMap::new();
    let mut previous = None;
    let mut ids = HashSet::new();
    for row in &rows {
        let key = group_key(row, GROUP)?;
        if previous.as_ref() != Some(&key) {
            if groups.contains_key(&key) {
                return err("a declared loop must occupy one contiguous block of records");
            };
            previous = Some(key.clone());
            ids.clear();
        }
        let sample = identifier(row.get("sample_id"), 128, false)?;
        if !ids.insert(sample) {
            return err("sample IDs must be unique within each declared loop");
        };
        if !row.get("branch_to_next").is_empty() {
            identifier(row.get("branch_to_next"), 128, false)?;
        }
        let v = coordinate(row.get("voltage"))? * &vf;
        let i = coordinate(row.get("current"))? * &cf;
        for value in [&v, &i] {
            bounded(value.clone())?;
            if !value.is_zero() && (value.abs() < ten(-150) || value.abs() > ten(150)) {
                return err("nonzero SI inputs must have magnitude between 1e-150 and 1e150");
            }
        }
        let group = groups.entry(key).or_default();
        group.push(Point {
            row: row.clone(),
            v,
            i,
        });
        if group.len() > 1000 || groups.len() > 1000 {
            return err("CSV exceeds loop or rows-per-loop limits");
        }
    }
    for ps in groups.values() {
        if !ps.last().unwrap().row.get("branch_to_next").is_empty()
            || ps[..ps.len() - 1]
                .iter()
                .any(|p| p.row.get("branch_to_next").is_empty())
        {
            return err("every nonfinal row needs an outgoing branch; final row must be empty");
        }
    }
    let results: Vec<Value> = groups
        .iter()
        .map(|(k, p)| audit(k, p))
        .collect::<Res<_>>()?;
    let rejected = results.iter().filter(|r| r["status"] == "rejected").count();
    Ok(
        json!({"schema_version":1,"tool":"iv-loop-integral","version":"0.1.0","parameters":{"voltage_unit":vu,"current_unit":cu,"accept_supplied_path_assumptions":true,"closure_rule":"exact_voltage_endpoints_no_added_edge","arithmetic":"exact_decimal_to_rational_then_binary64_summary","branch_rule":"explicit_outgoing_edge_labels_contiguous_nonreversing"},"provenance":{"input_sha256":digest,"input_byte_count":raw.len(),"group_columns":GROUP,"source_record_convention":"one-based data record index excluding header","source_line_convention":"one-based physical CSV lines including header","sample_index_convention":"one-based within declared loop","edge_index_convention":"edge i joins sample i to sample i+1; no added edge"},"counts":{"input_samples":rows.len(),"input_loops":results.len(),"usable_loops":results.len()-rejected,"rejected_loops":rejected,"input_edges":results.iter().map(|r|r["edge_count"].as_u64().unwrap()).sum::<u64>(),"input_branch_blocks":results.iter().map(|r|r["branch_audits"].as_array().unwrap().len()).sum::<usize>()},"assumptions_and_limits":constants("iv-loop-integral","LIMITS"),"results":results}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_strings() {
        assert_eq!(exact_decimal(&ten(-150)).unwrap(), "1e-150");
        assert_eq!(exact_decimal(&(-rat(125) * ten(-3))).unwrap(), "-1.25e-1");
        assert_eq!(exact_decimal(&rat(0)).unwrap(), "0");
        assert!(exact_decimal(&(rat(1) / rat(3))).is_err());
    }
    #[test]
    fn exact_resource_guards() {
        assert!(bounded(Rat::from_integer(BigInt::one() << 4096usize)).is_err());
        assert!(bounded(Rat::new(BigInt::one(), BigInt::one() << 4096usize)).is_err());
        assert!(exact_decimal(&(ten(1019) + rat(1))).is_err());
        assert!(exact_decimal(&(ten(1000) + rat(1))).is_ok());
    }
}
