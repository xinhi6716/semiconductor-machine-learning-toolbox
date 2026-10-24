// Shared exact arithmetic, bounded CSV, argument validation and private output.
pub use indexmap::IndexMap;
pub use num_bigint::BigInt;
pub use num_rational::BigRational;
pub use num_traits::{One, Signed, ToPrimitive, Zero};
pub use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    fs::OpenOptions,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::LazyLock,
};
pub type Rat = BigRational;
pub type Res<T> = Result<T, String>;
pub const MAX_BYTES: usize = 8 * 1024 * 1024;
pub fn py_trim(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
}
pub fn err<T>(msg: &str) -> Res<T> {
    Err(msg.to_owned())
}
pub fn rat(n: i64) -> Rat {
    Rat::from_integer(n.into())
}
pub fn ten(n: i64) -> Rat {
    if n >= 0 {
        Rat::from_integer(BigInt::from(10).pow(n as u32))
    } else {
        Rat::new(1.into(), BigInt::from(10).pow((-n) as u32))
    }
}
pub fn finite(v: &Rat) -> Res<f64> {
    let f = v
        .to_f64()
        .ok_or("numeric value is outside supported finite range")?;
    if !f.is_finite() || (f == 0.0 && !v.is_zero()) {
        err("numeric value is outside supported finite range")
    } else {
        Ok(f)
    }
}
pub fn f64rat(v: f64) -> Rat {
    Rat::from_float(v).expect("validated finite binary64")
}
pub fn sqrt_rat(v: &Rat) -> Res<f64> {
    sqrt_rat_precision(v, 50)
}
/// Round a rational to a prescribed decimal context by exact integer remainder.
/// This avoids a default-context division followed by a second decimal rounding.
pub fn rational_decimal_context(v: &Rat, precision: u64) -> bigdecimal::BigDecimal {
    if v.is_zero() {
        return bigdecimal::BigDecimal::from(0);
    }
    let absolute = v.abs();
    let mut exponent =
        v.numer().abs().to_string().len() as i64 - v.denom().to_string().len() as i64;
    if absolute < ten(exponent) {
        exponent -= 1
    }
    let scale = precision as i64 - 1 - exponent;
    let scaled = absolute * ten(scale);
    let mut q = scaled.numer() / scaled.denom();
    let remainder = scaled.numer() % scaled.denom();
    let twice = &remainder * 2u32;
    if twice > *scaled.denom() || (twice == *scaled.denom() && &q % 2u32 != BigInt::zero()) {
        q += 1u32
    }
    if v.is_negative() {
        q = -q
    }
    bigdecimal::BigDecimal::new(q, scale)
}
pub fn sqrt_rat_precision(v: &Rat, precision: u64) -> Res<f64> {
    if v.is_negative() {
        return err("negative squared quantity");
    }
    if v.is_zero() {
        return Ok(0.0);
    }
    let ctx = bigdecimal::Context::new(
        std::num::NonZeroU64::new(precision).ok_or("invalid decimal precision")?,
        bigdecimal::RoundingMode::HalfEven,
    );
    let decimal = rational_decimal_context(v, precision);
    let x = decimal
        .sqrt_with_context(&ctx)
        .ok_or("invalid square root")?;
    let f = x
        .to_string()
        .parse::<f64>()
        .map_err(|_| "square root numeric range error")?;
    if !f.is_finite() || f == 0.0 {
        err("square root numeric range error")
    } else {
        Ok(f)
    }
}
static NUMBER: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$").unwrap()
});
pub fn decimal_parts(
    text: &str,
    maxlen: usize,
    maxdigits: Option<usize>,
    maxexp: Option<i64>,
    expdigits: Option<usize>,
    trim: bool,
) -> Res<(Rat, i64, usize)> {
    let t = if trim { py_trim(text) } else { text };
    if t.len() > maxlen || !NUMBER.is_match(t) {
        return err("invalid numeric field");
    }
    let p = t.find(['e', 'E']);
    let (mant, ex) = if let Some(i) = p {
        (&t[..i], &t[i + 1..])
    } else {
        (t, "0")
    };
    if expdigits.is_some_and(|n| ex.trim_start_matches(['+', '-']).len() > n) {
        return err("numeric exponent syntax limit exceeded");
    }
    let exp: i64 = ex
        .parse()
        .map_err(|_| "numeric exponent outside supported range")?;
    let decimals = mant.find('.').map_or(0, |i| mant.len() - i - 1) as i64;
    let stored = exp
        .checked_sub(decimals)
        .ok_or("numeric exponent outside supported range")?;
    let negative = mant.starts_with('-');
    let digits: String = mant.chars().filter(|c| c.is_ascii_digit()).collect();
    let significant = digits.trim_start_matches('0');
    let count = significant.len().max(1);
    // Preserve CPython/libmpdec constructor limits, including signed zero tokens.
    if stored < -1_999_999_999_999_999_997i64
        || stored as i128 + count as i128 - 1 > 999_999_999_999_999_999i128
    {
        return err("numeric exponent outside supported range");
    }

    if maxdigits.is_some_and(|n| count > n)
        || maxexp.is_some_and(|n| stored.unsigned_abs() > n as u64)
    {
        return err("numeric precision or exponent limit exceeded");
    }
    // Decimal inputs outside binary64-required range can be rejected without allocating enormous integers.
    if significant.is_empty() {
        return Ok((Rat::zero(), stored, count));
    }
    if maxexp.is_none() && ((stored as i128 + count as i128 - 1).abs() > 10000) {
        return err("numeric exponent outside supported range");
    }
    let mut coeff = digits
        .parse::<BigInt>()
        .map_err(|_| "invalid decimal coefficient")?;
    if negative {
        coeff = -coeff
    }
    let value = Rat::from_integer(coeff) * ten(stored);
    Ok((value, stored, count))
}
pub fn decimal(text: &str) -> Res<Rat> {
    Ok(decimal_parts(text, 64, Some(32), Some(200), Some(4), true)?.0)
}
pub fn coordinate(text: &str) -> Res<Rat> {
    Ok(decimal_parts(text, 64, Some(32), Some(200), Some(4), false)?.0)
}
pub fn number(text: &str, factor: &Rat) -> Res<Rat> {
    let v = decimal(text)? * factor;
    if !v.is_zero() && (v.abs() < ten(-150) || v.abs() > ten(150)) {
        return err("nonzero SI input magnitude must be between 1e-150 and 1e150");
    }
    Ok(v)
}
pub fn number_float(text: &str, factor: &Rat) -> Res<f64> {
    let v = decimal_parts(text, 128, None, None, None, true)?.0 * factor;
    if v.is_zero() && py_trim(text).starts_with('-') {
        Ok(-0.0)
    } else {
        finite(&v)
    }
}
pub fn identifier(s: &str, max: usize, unicode_control: bool) -> Res<String> {
    if s.is_empty()
        || s != py_trim(s)
        || s.chars().count() > max
        || s.chars().any(|c| {
            if unicode_control {
                c.is_control()
            } else {
                (c as u32) < 32 || c == '\u{7f}'
            }
        })
    {
        err("identifiers must be bounded, nonempty, unpadded and control-free")
    } else {
        Ok(s.into())
    }
}
pub fn unit(name: &str, kind: &str) -> Res<Rat> {
    let power = match (kind, name) {
        ("voltage", "V")
        | ("current", "A")
        | ("field", "T")
        | ("length", "m")
        | ("resistance", "ohm") => 0,
        ("voltage", "mV") | ("current", "mA") | ("field", "mT") | ("length", "mm") => -3,
        ("voltage", "uV") | ("current", "uA") | ("field", "uT") | ("length", "um") => -6,
        ("voltage", "nV") | ("current", "nA") | ("length", "nm") => -9,
        ("current", "pA") => -12,
        ("field", "G") => -4,
        ("field", "kG") => -1,
        ("resistance", "kohm") => 3,
        ("resistance", "Mohm") => 6,
        _ => return err("unsupported unit"),
    };
    Ok(ten(power))
}
pub fn sha256(raw: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(raw))
}
pub fn read_regular(path: &Path, max: usize) -> Res<Vec<u8>> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let f = options
        .open(path)
        .map_err(|_| "cannot read requested regular input")?;
    let meta = f.metadata().map_err(|_| "cannot inspect requested input")?;
    if !meta.is_file() || meta.len() > max as u64 {
        return err("input must be a bounded regular file");
    }
    let mut raw = Vec::new();
    f.take((max + 1) as u64)
        .read_to_end(&mut raw)
        .map_err(|_| "cannot read requested input")?;
    if raw.len() > max {
        return err("input byte limit exceeded");
    }
    Ok(raw)
}
#[derive(Debug, Clone)]
pub struct CsvRecord {
    pub fields: Vec<Vec<u8>>,
    pub line_start: usize,
    pub line_end: usize,
    pub bytes: usize,
}
pub fn csv_records(
    raw: &[u8],
    opaque_strict: bool,
    record_limit: Option<usize>,
) -> Res<Vec<CsvRecord>> {
    csv_records_limited(
        raw,
        opaque_strict,
        record_limit,
        usize::MAX,
        usize::MAX,
        false,
        None,
    )
}
pub fn csv_records_limited(
    raw: &[u8],
    opaque_strict: bool,
    record_limit: Option<usize>,
    max_fields: usize,
    max_records: usize,
    skip_blank: bool,
    selected_names: Option<&[&str]>,
) -> Res<Vec<CsvRecord>> {
    csv_records_impl(
        raw,
        opaque_strict,
        record_limit,
        max_fields,
        max_records,
        skip_blank,
        selected_names,
        true,
    )
}
pub fn csv_records_bosch(raw: &[u8], selected_names: &[&str]) -> Res<Vec<CsvRecord>> {
    csv_records_impl(
        raw,
        true,
        Some(65536),
        11,
        2001,
        false,
        Some(selected_names),
        false,
    )
}
fn csv_records_impl(
    raw: &[u8],
    opaque_strict: bool,
    record_limit: Option<usize>,
    max_fields: usize,
    max_records: usize,
    skip_blank: bool,
    selected_names: Option<&[&str]>,
    count_terminator: bool,
) -> Res<Vec<CsvRecord>> {
    let mut selected: Option<HashSet<usize>> = None;
    let mut pos = if raw.starts_with(&[239, 187, 191]) {
        3
    } else {
        0
    };
    let mut line = 1;
    let mut records = vec![];
    while pos < raw.len() {
        let start = pos;
        let line_start = line;
        let mut fields = vec![];
        if !opaque_strict && matches!(raw[pos], b'\n' | b'\r') {
            if raw[pos] == b'\r' && raw.get(pos + 1) == Some(&b'\n') {
                pos += 1
            }
            pos += 1;
            if !skip_blank || records.is_empty() {
                records.push(CsvRecord {
                    fields,
                    line_start,
                    line_end: line,
                    bytes: pos - start,
                });
            }
            if records.len() > max_records {
                return err("CSV record limit exceeded");
            }
            line += 1;
            continue;
        }
        loop {
            let mut cell = vec![];
            let keep = selected
                .as_ref()
                .is_none_or(|indices| indices.contains(&fields.len()));
            if raw.get(pos) == Some(&b'"') {
                pos += 1;
                loop {
                    let c = *raw.get(pos).ok_or("unclosed CSV quote")?;
                    pos += 1;
                    if c == b'"' {
                        if raw.get(pos) == Some(&b'"') {
                            if keep {
                                cell.push(c);
                            }
                            pos += 1;
                        } else {
                            break;
                        }
                    } else {
                        if keep {
                            cell.push(c);
                        }
                        if c == b'\n' || (c == b'\r' && raw.get(pos) != Some(&b'\n')) {
                            line += 1;
                        }
                    }
                    if record_limit.is_some_and(|m| pos - start > m) {
                        return err("CSV record byte limit exceeded");
                    }
                }
                if pos < raw.len() && !matches!(raw[pos], b',' | b'\n' | b'\r') {
                    return err("bytes after quoted CSV field");
                }
            } else {
                while pos < raw.len() && !matches!(raw[pos], b',' | b'\n' | b'\r') {
                    let c = raw[pos];
                    if opaque_strict && c == b'"' {
                        return err("quote inside unquoted CSV field");
                    }
                    if keep {
                        cell.push(c);
                    }
                    pos += 1;
                    if record_limit.is_some_and(|m| pos - start > m) {
                        return err("CSV record byte limit exceeded");
                    }
                }
            }
            fields.push(cell);
            if fields.len() > max_fields {
                return err("CSV field-count limit exceeded");
            }
            if raw.get(pos) == Some(&b',') {
                pos += 1;
                continue;
            }
            break;
        }
        let line_end = line;
        let content_end = pos;
        if raw.get(pos) == Some(&b'\r') {
            pos += 1;
            if raw.get(pos) == Some(&b'\n') {
                pos += 1
            } else if opaque_strict {
                return err("bare CSV carriage return");
            }
            line += 1;
        } else if raw.get(pos) == Some(&b'\n') {
            pos += 1;
            line += 1;
        }
        if record_limit
            .is_some_and(|m| (if count_terminator { pos } else { content_end }) - start > m)
        {
            return err("CSV record byte limit exceeded");
        };
        if records.is_empty() {
            if let Some(names) = selected_names {
                selected = Some(
                    fields
                        .iter()
                        .enumerate()
                        .filter(|(_, v)| names.iter().any(|n| n.as_bytes() == v.as_slice()))
                        .map(|(i, _)| i)
                        .collect(),
                );
            }
        }
        records.push(CsvRecord {
            fields,
            line_start,
            line_end,
            bytes: pos - start,
        });
        if records.len() > max_records {
            return err("CSV record limit exceeded");
        }
    }
    Ok(records)
}
#[derive(Debug, Clone)]
pub struct Row {
    pub data: IndexMap<String, String>,
    pub line_start: usize,
    pub line_end: usize,
    pub ordinal: usize,
}
impl Row {
    pub fn get(&self, key: &str) -> &str {
        &self.data[key]
    }
}
pub fn load_rows(path: &Path, columns: &[&str], skip_blank: bool) -> Res<(Vec<Row>, String)> {
    load_rows_bounded(path, columns, skip_blank, MAX_BYTES, 10000)
}
pub fn load_rows_bounded(
    path: &Path,
    columns: &[&str],
    skip_blank: bool,
    max_bytes: usize,
    max_rows: usize,
) -> Res<(Vec<Row>, String)> {
    let raw = read_regular(path, max_bytes)?;
    parse_rows(&raw, columns, skip_blank, max_rows)
}
pub fn parse_rows(
    raw: &[u8],
    columns: &[&str],
    skip_blank: bool,
    max_rows: usize,
) -> Res<(Vec<Row>, String)> {
    std::str::from_utf8(raw).map_err(|_| "invalid UTF-8 CSV input")?;
    let records = csv_records_limited(
        raw,
        false,
        None,
        columns.len(),
        max_rows.saturating_add(1),
        skip_blank,
        None,
    )?;
    let h = records.first().ok_or("CSV header missing")?;
    let header: Vec<String> = h
        .fields
        .iter()
        .map(|f| String::from_utf8(f.clone()).map_err(|_| "invalid UTF-8 header".to_owned()))
        .collect::<Res<_>>()?;
    if header.len() != columns.len()
        || header.iter().collect::<HashSet<_>>().len() != columns.len()
        || !columns.iter().all(|s| header.iter().any(|h| h == s))
    {
        return err("CSV header must contain exactly the documented columns");
    }
    let mut rows = vec![];
    for r in records.into_iter().skip(1) {
        if skip_blank && r.fields.is_empty() {
            continue;
        }
        if r.fields.len() != header.len() {
            return err("CSV row width does not match header");
        };
        let vals: Vec<String> = r
            .fields
            .into_iter()
            .map(|v| String::from_utf8(v).map_err(|_| "invalid UTF-8 CSV field".to_owned()))
            .collect::<Res<_>>()?;
        if vals.iter().any(|v| v.chars().count() > 131072) {
            return err("CSV field exceeds supported character limit");
        };
        rows.push(Row {
            data: header.iter().cloned().zip(vals).collect(),
            line_start: r.line_start,
            line_end: r.line_end,
            ordinal: rows.len() + 1,
        });
        if rows.len() > max_rows {
            return err("CSV row limit exceeded");
        }
    }
    if rows.is_empty() {
        return err("CSV contains no data rows");
    };
    Ok((rows, sha256(&raw)))
}
pub fn group_key(row: &Row, cols: &[&str]) -> Res<Vec<String>> {
    cols.iter()
        .map(|c| identifier(row.get(c), 128, false))
        .collect()
}
pub fn identity(cols: &[&str], key: &[String]) -> Value {
    Value::Object(
        cols.iter()
            .zip(key)
            .map(|(c, k)| (c.to_string(), json!(k)))
            .collect(),
    )
}
pub fn constants(tool: &str, name: &str) -> Value {
    static C: LazyLock<Value> =
        LazyLock::new(|| serde_json::from_str(include_str!("reference_constants.json")).unwrap());
    C[tool][name].clone()
}
pub fn counts(results: &[Value], rows: usize, groups: usize) -> Value {
    let rejected = results.iter().filter(|r| r["status"] == "rejected").count();
    let mut reasons = serde_json::Map::new();
    for r in results {
        if r["status"] == "rejected" {
            let k = r["reason"].as_str().unwrap().to_owned();
            let count = reasons.get(&k).and_then(Value::as_u64).unwrap_or(0);
            reasons.insert(k, json!(count + 1));
        }
    }
    json!({"input_rows":rows,"input_groups":groups,"extracted_groups":results.len()-rejected,"rejected_groups":rejected,"rejected_by_reason":reasons})
}
#[derive(Debug)]
pub struct Args {
    pub input: PathBuf,
    pub options: HashMap<String, String>,
    pub flags: HashSet<String>,
}
impl Args {
    pub fn req(&self, key: &str) -> Res<&str> {
        self.options
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| format!("required option --{} missing", key))
    }
    pub fn opt(&self, key: &str) -> Option<&str> {
        self.options.get(key).map(String::as_str)
    }
    pub fn flag(&self, key: &str) -> bool {
        self.flags.contains(key)
    }
    pub fn choice<'a>(&'a self, key: &str, values: &[&str]) -> Res<&'a str> {
        let v = self.req(key)?;
        if values.contains(&v) {
            Ok(v)
        } else {
            err("unsupported command-line option value")
        }
    }
    pub fn require_flag(&self, key: &str) -> Res<()> {
        if self.flag(key) {
            Ok(())
        } else {
            err("explicit acceptance of documented assumptions required")
        }
    }
}
pub fn parse_args(args: &[String], allowed: &[&str], flags: &[&str]) -> Res<Args> {
    let mut input = None;
    let mut options = HashMap::new();
    let mut found = HashSet::new();
    let mut i = 0;
    let mut positional = false;
    while i < args.len() {
        let arg = &args[i];
        if arg == "--" && !positional {
            positional = true;
            i += 1;
            continue;
        }
        if let Some(opt) = arg.strip_prefix("--").filter(|_| !positional) {
            let (k, val) = opt
                .split_once('=')
                .map(|(k, v)| (k, Some(v)))
                .unwrap_or((opt, None));
            if flags.contains(&k) || k == "stdout" {
                if val.is_some() {
                    return err("invalid command-line option");
                };
                found.insert(k.into());
            } else if allowed.contains(&k) || k == "output" {
                let val = if let Some(v) = val {
                    v.to_owned()
                } else {
                    i += 1;
                    args.get(i)
                        .filter(|v| !v.starts_with("--"))
                        .ok_or("missing command-line option value")?
                        .clone()
                };
                options.insert(k.into(), val);
            } else {
                return err("invalid command-line option; use --help");
            }
        } else if input.is_none() {
            input = Some(PathBuf::from(arg))
        } else {
            return err("unexpected command-line argument");
        };
        i += 1;
    }
    if found.contains("stdout") == options.contains_key("output") {
        return err("choose exactly one destination: --stdout or --output");
    };
    Ok(Args {
        input: input.ok_or("input CSV path required")?,
        options,
        flags: found,
    })
}
pub fn write_report(report: &Value, args: &Args, maximum: usize) -> Res<()> {
    let mut payload =
        serde_json::to_vec_pretty(report).map_err(|_| "unable to serialize report")?;
    if report["tool"] == "rram-window"
        || report["schema_version"] == "bosch-identity-audit-v1"
        || report["schema_version"] == "iv-grid-audit-v1"
    {
        let text = String::from_utf8(payload).map_err(|_| "unable to serialize report")?;
        let mut escaped = String::with_capacity(text.len());
        for c in text.chars() {
            if (c as u32) > 126 {
                for unit in c.encode_utf16(&mut [0; 2]) {
                    use std::fmt::Write;
                    write!(&mut escaped, "\\u{:04x}", unit)
                        .map_err(|_| "unable to serialize report")?;
                }
            } else {
                escaped.push(c)
            }
        }
        payload = escaped.into_bytes();
    }
    payload.push(b'\n');
    if payload.len() > maximum {
        return err("complete report byte limit exceeded");
    };
    if args.flag("stdout") {
        let mut out = std::io::stdout().lock();
        out.write_all(&payload)
            .and_then(|_| out.flush())
            .map_err(|_| "unable to write report")?;
    } else {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut f = options
            .open(args.req("output")?)
            .map_err(|_| "unable to create new report; existing outputs never overwritten")?;
        f.write_all(&payload)
            .map_err(|_| "unable to write complete new report")?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_decimal_order() {
        assert!(
            coordinate("1.0000000000000000000000000000001").unwrap() > coordinate("1").unwrap()
        );
        assert_eq!(coordinate("1e-200").unwrap(), ten(-200));
        assert!(coordinate("1e-201").is_err());
        assert!(coordinate(" 1").is_err());
    }
    #[test]
    fn numeric_underflow_is_rejected() {
        assert!(finite(&ten(-400)).is_err());
        assert_eq!(finite(&Rat::zero()).unwrap(), 0.0);
    }
    #[test]
    fn csv_physical_line_tracking() {
        let rs = csv_records(b"a,b\r\n\"x\ny\",z\n", false, None).unwrap();
        assert_eq!(rs[1].line_start, 2);
        assert_eq!(rs[1].line_end, 3);
        assert!(csv_records(b"a\"b,c", true, None).is_err());
    }
    #[test]
    fn output_bound_rejects_before_creating_file() {
        let path = std::env::temp_dir().join(format!(
            "smlt-output-bound-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let a = Args {
            input: PathBuf::new(),
            options: HashMap::from([("output".into(), path.to_string_lossy().into_owned())]),
            flags: HashSet::new(),
        };
        assert!(write_report(&json!({"large":"payload"}), &a, 1).is_err());
        assert!(!path.exists());
    }
    #[test]
    fn opaque_fields_are_scanned_without_retention() {
        let records = csv_records_limited(
            b"id,response\na,\"private,\xffdata\"\n",
            true,
            Some(100),
            2,
            2,
            false,
            Some(&["id"]),
        )
        .unwrap();
        assert_eq!(records[1].fields[0], b"a");
        assert!(records[1].fields[1].is_empty());
    }
    #[test]
    fn parser_resource_guards_before_materialization() {
        let raw = [b"a,b\n".as_slice(), &vec![b'\n'; 1_000_000], b"1,2\n"].concat();
        let records = csv_records_limited(&raw, false, None, 2, 2, true, None).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[1].line_end, 1_000_002);
        assert!(
            csv_records_limited(&vec![b','; 1_000_000], false, None, 2, 2, false, None).is_err()
        );
        assert!(csv_records_limited(b"a\n1\n2\n", false, None, 1, 2, false, None).is_err());
    }
    #[test]
    fn signed_zero_and_nonzero_range() {
        assert!(number_float("-0", &rat(1)).unwrap().is_sign_negative());
        assert!(finite(&ten(400)).is_err());
        assert!(finite(&ten(-400)).is_err());
    }
    #[test]
    fn prescribed_decimal_sqrt_context_avoids_false_extra_accuracy() {
        let midpoint = Rat::new((BigInt::one() << 53usize) + 1u32, BigInt::one() << 53usize);
        let square = &midpoint * &midpoint;
        assert_eq!(
            sqrt_rat_precision(&(&square + ten(-60)), 50)
                .unwrap()
                .to_bits(),
            1.0f64.to_bits()
        );
        assert_eq!(
            sqrt_rat_precision(&(&square + ten(-80)), 80)
                .unwrap()
                .to_bits(),
            1.0f64.to_bits()
        );
    }
}
