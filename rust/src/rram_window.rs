use crate::common::*;
use std::collections::BTreeMap;
const COLUMNS: &[&str] = &[
    "device",
    "cycle",
    "state",
    "read_condition",
    "read_voltage",
    "resistance",
];
const COUNT_KEYS: &[&str] = &[
    "rows",
    "device_cycle_groups",
    "complete_pairs",
    "missing_pair_groups",
    "missing_hrs_groups",
    "missing_lrs_groups",
    "invalid_resistance_rows",
    "invalid_read_voltage_rows",
    "zero_read_voltage_pairs",
    "voltage_mismatch_pairs",
    "read_condition_mismatch_pairs",
    "unrepresentable_ratio_pairs",
    "valid_pairs",
    "excluded_groups",
    "ratios_below_one_pairs",
];
fn zeros() -> Value {
    Value::Object(
        COUNT_KEYS
            .iter()
            .map(|k| (k.to_string(), json!(0)))
            .collect(),
    )
}
fn inc(v: &mut Value, key: &str, n: usize) {
    v[key] = json!(v[key].as_u64().unwrap_or(0) + n as u64);
}
#[derive(Clone)]
struct Obs {
    line: usize,
    condition: String,
    r: Option<f64>,
    v: Option<f64>,
    exact: Option<Rat>,
    text: Option<String>,
    issues: Vec<String>,
}
impl Obs {
    fn public(&self) -> Value {
        json!({"source_line":self.line,"read_condition":self.condition,"resistance_ohm":self.r,"read_voltage_V":self.v,"read_voltage_input":self.text,"issues":self.issues})
    }
}
fn decimal_string(raw: &str) -> Res<String> {
    let s = raw.trim_start_matches(['+', '-']);
    let neg = raw.starts_with('-');
    let (mant, ex) = s.split_once(['e', 'E']).unwrap_or((s, "0"));
    let exp: i64 = ex.parse().map_err(|_| "not_decimal_number")?;
    let decimals = mant.find('.').map_or(0, |i| mant.len() - i - 1);
    let digs: String = mant.chars().filter(|c| *c != '.').collect();
    let digits = digs.trim_start_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    let exponent = exp - decimals as i64;
    let adjusted = exponent + digits.len() as i64 - 1;
    let body = if exponent <= 0 && adjusted >= -6 {
        let point = digits.len() as i64 + exponent;
        if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            digits.to_string()
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        }
    } else {
        format!(
            "{}{}E{:+}",
            &digits[..1],
            if digits.len() > 1 {
                format!(".{}", &digits[1..])
            } else {
                String::new()
            },
            adjusted
        )
    };
    Ok(if neg { format!("-{}", body) } else { body })
}
fn value(
    raw: &str,
    factor: &Rat,
    positive: bool,
) -> (
    Option<f64>,
    Option<Rat>,
    Option<String>,
    Option<&'static str>,
) {
    if raw.is_empty() {
        return (None, None, None, Some("missing"));
    }
    let pattern =
        regex::Regex::new(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$").unwrap();
    if !pattern.is_match(raw) {
        return (None, None, None, Some("not_decimal_number"));
    }
    // CPython's Decimal constructor has finite exponent storage even for zero.
    let (mant, exponent_text) = raw.split_once(['e', 'E']).unwrap_or((raw, "0"));
    let exponent = match exponent_text.parse::<i128>() {
        Ok(v) => v,
        Err(_) => return (None, None, None, Some("not_decimal_number")),
    };
    let decimals = mant.find('.').map_or(0, |i| mant.len() - i - 1) as i128;
    let stored = exponent - decimals;
    let ds: String = mant.chars().filter(char::is_ascii_digit).collect();
    let significant = ds.trim_start_matches('0').len().max(1) as i128;
    let adjusted = stored + significant - 1;
    let factor_exponent = if factor >= &Rat::one() {
        factor.numer().to_string().len() as i128 - 1
    } else {
        -(factor.denom().to_string().len() as i128 - 1)
    };
    if adjusted > 999_999_999_999_999_999i128
        || adjusted + factor_exponent > 999_999_999_999_999_999i128
        || stored + factor_exponent < -1_999_999_999_999_999_997i128
    {
        return (None, None, None, Some("not_decimal_number"));
    }

    if !(-1_999_999_999_999_999_997i128..=999_999_999_999_999_999i128).contains(&stored) {
        return (None, None, None, Some("not_decimal_number"));
    }
    // Reference checks positivity after Decimal construction but before finite-float range.
    if positive && (mant.starts_with('-') || ds.trim_start_matches('0').is_empty()) {
        return (None, None, None, Some("not_positive"));
    }
    let parsed = decimal_parts(raw, 131072, None, None, None, false);
    let exact = match parsed {
        Ok((r, _, _)) => r,
        Err(_) => return (None, None, None, Some("nonfinite_or_out_of_range")),
    };
    if positive && exact <= Rat::zero() {
        return (None, None, None, Some("not_positive"));
    };
    let normalized = match finite(&(&exact * factor)) {
        Ok(f) => f,
        Err(_) => return (None, None, None, Some("nonfinite_or_out_of_range")),
    };
    let normalized = if normalized == 0.0 && raw.starts_with('-') {
        -0.0
    } else {
        normalized
    };
    let text = match decimal_string(raw) {
        Ok(s) => s,
        Err(_) => return (None, None, None, Some("not_decimal_number")),
    };
    (Some(normalized), Some(exact), Some(text), None)
}
fn summary(values: &[f64]) -> Value {
    if values.is_empty() {
        return json!({"count":0,"minimum":null,"median":null,"maximum":null,"arithmetic_mean":null});
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let n = ordered.len();
    let mut median = ordered[n / 2];
    if n % 2 == 0 {
        let low = ordered[n / 2 - 1];
        median = low + (median - low) / 2.0
    }
    let maximum = ordered[n - 1];
    let exact_sum: Rat = ordered.iter().map(|v| f64rat(v / maximum)).sum();
    let scaled = exact_sum.to_f64().unwrap() / n as f64;
    let mean = maximum * scaled;
    json!({"count":n,"minimum":ordered[0],"median":median,"maximum":maximum,"arithmetic_mean":mean})
}
struct Device {
    counts: Value,
    ratios: Vec<f64>,
    buckets: BTreeMap<(String, Rat), (String, f64, Vec<f64>)>,
}
pub fn run(a: &Args) -> Res<Value> {
    let vu = a.choice("voltage-unit", &["V", "mV", "uV"])?;
    let ru = a.choice("resistance-unit", &["ohm", "kohm", "Mohm"])?;
    let hrs = a.req("hrs-label")?;
    let lrs = a.req("lrs-label")?;
    identifier(hrs, usize::MAX, false)?;
    identifier(lrs, usize::MAX, false)?;
    if hrs == lrs {
        return err("HRS and LRS labels must be distinct");
    };
    let (rows, _) = load_rows_bounded(&a.input, COLUMNS, false, usize::MAX - 1, usize::MAX)?;
    let mut groups: BTreeMap<(String, String), IndexMap<String, Obs>> = BTreeMap::new();
    let vf = unit(vu, "voltage")?;
    let rf = unit(ru, "resistance")?;
    for row in &rows {
        if row.data.values().any(|v| v.contains('\0')) {
            return err("NUL characters are not allowed");
        };
        for c in ["device", "cycle", "state", "read_condition"] {
            identifier(row.get(c), usize::MAX, false)?;
        }
        let state = row.get("state");
        if state != hrs && state != lrs {
            return err("state does not match either explicit state label");
        };
        let states = groups
            .entry((row.get("device").into(), row.get("cycle").into()))
            .or_default();
        if states.contains_key(state) {
            return err("duplicate device/cycle/state observation");
        };
        let (r, _, _, re) = value(py_trim(row.get("resistance")), &rf, true);
        let (v, exact, text, ve) = value(py_trim(row.get("read_voltage")), &vf, false);
        let mut issues = vec![];
        if let Some(e) = re {
            issues.push(format!("resistance:{}", e))
        }
        if let Some(e) = ve {
            issues.push(format!("read_voltage:{}", e))
        };
        states.insert(
            state.into(),
            Obs {
                line: row.line_end,
                condition: row.get("read_condition").into(),
                r,
                v,
                exact,
                text,
                issues,
            },
        );
    }
    let mut devices: BTreeMap<String, Device> = BTreeMap::new();
    let mut pairs = vec![];
    for ((device, cycle), states) in groups {
        let record = devices.entry(device.clone()).or_insert_with(|| Device {
            counts: zeros(),
            ratios: vec![],
            buckets: BTreeMap::new(),
        });
        let c = &mut record.counts;
        inc(c, "device_cycle_groups", 1);
        inc(c, "rows", states.len());
        inc(
            c,
            "invalid_resistance_rows",
            states.values().filter(|o| o.r.is_none()).count(),
        );
        inc(
            c,
            "invalid_read_voltage_rows",
            states.values().filter(|o| o.v.is_none()).count(),
        );
        let h = states.get(hrs);
        let l = states.get(lrs);
        let mut reasons: Vec<&str> = vec![];
        let mut ratio = None;
        let mut common_voltage = None;
        if h.is_none() || l.is_none() {
            inc(c, "missing_pair_groups", 1);
            if h.is_none() {
                inc(c, "missing_hrs_groups", 1);
                reasons.push("missing_hrs")
            }
            if l.is_none() {
                inc(c, "missing_lrs_groups", 1);
                reasons.push("missing_lrs")
            }
        } else {
            inc(c, "complete_pairs", 1)
        }
        if states.values().any(|o| o.r.is_none()) {
            reasons.push("invalid_resistance")
        }
        if states.values().any(|o| o.v.is_none()) {
            reasons.push("invalid_read_voltage")
        }
        if let (Some(h), Some(l)) = (h, l) {
            if h.condition != l.condition {
                inc(c, "read_condition_mismatch_pairs", 1);
                reasons.push("read_condition_mismatch")
            }
            if h.v == Some(0.0) || l.v == Some(0.0) {
                inc(c, "zero_read_voltage_pairs", 1);
                reasons.push("zero_read_voltage")
            }
            if let (Some(hv), Some(lv)) = (&h.exact, &l.exact) {
                if hv != lv {
                    inc(c, "voltage_mismatch_pairs", 1);
                    reasons.push("read_voltage_mismatch")
                } else if h.v != Some(0.0) {
                    common_voltage = h.v
                }
            }
            if reasons.is_empty() {
                let r = h.r.unwrap() / l.r.unwrap();
                if !r.is_finite() || r <= 0.0 {
                    inc(c, "unrepresentable_ratio_pairs", 1);
                    reasons.push("ratio_out_of_range")
                } else {
                    ratio = Some(r)
                }
            }
        }
        let valid = reasons.is_empty();
        if valid {
            let r = ratio.unwrap();
            inc(c, "valid_pairs", 1);
            inc(c, "ratios_below_one_pairs", usize::from(r < 1.0));
            record.ratios.push(r);
            let h = h.unwrap();
            let bucket = record
                .buckets
                .entry((h.condition.clone(), h.exact.clone().unwrap()))
                .or_insert_with(|| (h.text.clone().unwrap(), common_voltage.unwrap(), vec![]));
            bucket.2.push(r);
        } else {
            inc(c, "excluded_groups", 1)
        }
        pairs.push(json!({"device":device,"cycle":cycle,"status":if valid{"valid"}else{"excluded"},"reasons":reasons,"hrs":h.map(Obs::public),"lrs":l.map(Obs::public),"common_read_voltage_V":common_voltage,"resistance_ratio":ratio,"ratio_below_one":ratio.map(|r|r<1.0)}));
    }
    let mut totals = zeros();
    let mut output = vec![];
    for (device, record) in &devices {
        for &key in COUNT_KEYS {
            inc(
                &mut totals,
                key,
                record.counts[key].as_u64().unwrap() as usize,
            )
        }
        let voltage_summaries:Vec<Value>=record.buckets.iter().map(|((condition,_),(text,v,ratios))|json!({"read_condition":condition,"read_voltage_input":text,"read_voltage_V":v,"resistance_ratio_summary":summary(ratios)})).collect();
        output.push(json!({"device":device,"counts":record.counts,"resistance_ratio_summary":if voltage_summaries.len()>1{Value::Null}else{summary(&record.ratios)},"by_read_condition_and_voltage":voltage_summaries,"mixed_read_conditions_or_voltages":record.buckets.len()>1}));
    }
    totals["devices"] = json!(devices.len());
    Ok(
        json!({"schema_version":"1.0","tool":"rram-window","input":{"source":a.input.to_string_lossy(),"voltage_unit":vu,"resistance_unit":ru,"hrs_label":hrs,"lrs_label":lrs},"method":{"ratio":"R_HRS / R_LRS","read_voltage_match":"exact decimal equality under common SI scaling, signed, nonzero","read_condition_match":"exact user-declared condition label equality; actual measurement protocol is not verified","normalized_units":{"read_voltage":"V","resistance":"ohm","resistance_ratio":"dimensionless"},"cycle_interpretation":"repeated observations within a device, not independent samples","summary_interpretation":"descriptive only; consult by_read_condition_and_voltage before combining conditions"},"counts":totals,"per_device":output,"pairs":pairs}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decimal_display() {
        for (t, e) in [
            ("1.00", "1.00"),
            ("1e2", "1E+2"),
            ("00100", "100"),
            ("-0.00", "-0.00"),
            ("0e-7", "0E-7"),
            (".000001", "0.000001"),
        ] {
            assert_eq!(decimal_string(t).unwrap(), e)
        }
    }
    #[test]
    fn nonpositive_resistance_precedes_float_range() {
        for raw in [
            "-1e20001",
            "-1e-20001",
            "-1e999999999999999999",
            "-1e-1999999999999999997",
        ] {
            assert_eq!(value(raw, &rat(1), true).3, Some("not_positive"));
        }
        assert_eq!(
            value("-1e999999999999999999999999", &rat(1), true).3,
            Some("not_decimal_number")
        );
    }
}
