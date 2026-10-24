//! Native, fail-closed measurement-to-feature workflows. Scientific extraction
//! is delegated to the existing Rust core modules, never to another process.
//! Provenance schema 2 identifies compiled Rust sources rather than Python.
use crate::common::*;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const MANIFEST: &[&str] = &[
    "batch",
    "wafer",
    "device",
    "cycle",
    "replicate",
    "branch",
    "state",
    "condition",
];
const CONDITIONS: &[&str] = &[
    "condition",
    "read_voltage_V",
    "temperature_K",
    "read_delay_ms",
    "protocol",
];
const IV: &[&str] = &["device", "cycle", "branch", "voltage", "current"];
const RRAM_INPUTS: &[&str] = &["manifest.csv", "conditions.csv", "iv_sweeps.csv"];
const RESISTANCE: &[&str] = &[
    "device",
    "cycle",
    "state",
    "read_condition",
    "read_voltage",
    "resistance",
];
const RRAM_FEATURES: &[&str] = &[
    "hrs_resistance_median_ohm",
    "lrs_resistance_median_ohm",
    "resistance_ratio_median",
];
const RRAM_OUTPUT: &[&str] = &[
    "batch",
    "wafer",
    "device",
    "condition",
    "split_group",
    "read_voltage_V",
    "cycle_count",
    "ratio_below_one_cycle_count",
    "hrs_resistance_median_ohm",
    "lrs_resistance_median_ohm",
    "resistance_ratio_median",
];
const FET_GROUP: &[&str] = &["lot", "wafer", "device", "condition", "replicate", "branch"];
const FET_FEATURES: &[&str] = &[
    "criterion_gate_source_voltage_V",
    "log_current_slope_decades_per_V",
];
const FET_METADATA: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "replicate",
    "branch",
    "split_group",
    "temperature_K",
    "drain_source_voltage_V",
    "body_source_voltage_V",
    "signed_current_criterion_A",
    "window_min_V",
    "window_max_V",
    "max_bracket_span_V",
    "branch_sample_count",
    "log_selected_sample_count",
    "criterion_method",
    "criterion_bracket_span_V",
];
const FET_OPTIONS: &[&str] = &[
    "output-dir",
    "voltage-unit",
    "current-unit",
    "criterion-current",
    "criterion-current-unit",
    "current-polarity",
    "max-bracket-span",
    "window-min",
    "window-max",
];
type Payloads = IndexMap<String, Vec<u8>>;

pub fn help(tool: &str) -> &'static str {
    if tool == "rram-features" {
        "smlt rram-features: native RRAM measurement-to-device features\n\nUsage: smlt rram-features --input-dir INPUT_DIR --output-dir NEW_DIR \\\n  --max-bracket-span-V POSITIVE_VOLTS --accept-measurement-assumptions\n\nInput directory: manifest.csv, conditions.csv, iv_sweeps.csv.\nI-V voltage/current are SI V/A only. Declared state, physical device, fixed\ncondition and paired repeats are mandatory; no state or unit inference.\nThe flag accepts signed, state-preserving fixed-protocol measurements and\nlocal linear-current interpolation within the supplied bound, plus positive\nstatic terminal resistance R=V/I. No absolute-value sign repair or extrapolation.\nAll joins and extractions must pass before creating six private output files.\nExit 0: complete software export; exit 2: invalid input/arguments or I/O error.\nOutput directories must not exist; parent directories must already exist.\nRequested POSIX modes: directories 0700, files 0600. I/O failures can leave\npartial new outputs; existing destinations and symlinks are never overwritten.\nWindows uses inherited ACLs; Windows runtime behavior is not validated here.\nProvenance schema 2 records compiled Rust-source hashes, not Python execution.\nNo target, trained model, physical validation, network or Python runtime.\n"
    } else {
        "smlt fet-features: native shared-transfer branch features\n\nUsage: smlt fet-features INPUT.csv --output-dir NEW_DIR \\\n  --voltage-unit V|mV|uV --current-unit A|mA|uA|nA|pA \\\n  --criterion-current SIGNED_VALUE --criterion-current-unit A|mA|uA|nA|pA \\\n  --current-polarity positive|negative --max-bracket-span POSITIVE_VALUE \\\n  --window-min VALUE --window-max VALUE --accept-measurement-assumptions\n\nVoltage unit applies to gate/drain/body voltages, bracket span and inclusive\nwindow bounds. The current criterion has its own explicit unit.\nThe flag accepts both native Vth and log-slope tools' measurement assumptions:\nsource-referenced signed terminals, fixed temperature/bias/state/protocol,\ncorrect polarity, justified criterion/window and local linear interpolation.\nBoth extractors consume one unchanged private snapshot. Rows are acquisition\nbranches, not independent devices; physical identity is lot/wafer/device.\nExit 0: eight complete files. Exit 1: five audit-only files if any branch fails\neither extractor; no partial feature table, predictors or allowlist is emitted.\nExit 2: fatal argument, input, integrity or I/O error.\nOutput directories must not exist; parent directories must already exist.\nRequested POSIX modes: directories 0700, files 0600. I/O failures can leave\npartial new outputs; existing destinations and symlinks are never overwritten.\nWindows uses inherited ACLs; Windows runtime behavior is not validated here.\nProvenance schema 2 records compiled Rust-source hashes, not Python execution.\nNo target, trained model, physical validation, network or Python runtime.\n"
    }
}

/// The top-level CLI routes workflow commands here before its single-report parser.
pub fn execute(tool: &str, argv: &[String]) -> Res<i32> {
    if tool != "rram-features" && tool != "fet-features" {
        return err("unknown feature workflow");
    }
    let normalized = normalize_workflow_args(tool, argv)?;
    let argv = normalized.as_slice();
    let control = &argv[..argv.iter().position(|v| v == "--").unwrap_or(argv.len())];
    if control.iter().any(|v| v == "--help" || v == "-h") {
        write_stdout(help(tool))?;
        return Ok(0);
    }
    let args = parse_workflow_args(tool, argv)?;
    args.require_flag("accept-measurement-assumptions")?;
    let (payloads, code) = if tool == "rram-features" {
        (build_rram(&args)?, 0)
    } else {
        build_fet(&args)?
    };
    write_outputs(Path::new(args.req("output-dir")?), &payloads)?;
    write_stdout(if tool == "rram-features" {
        "Created a device-level feature table and five provenance/schema reports. No model was trained.\n"
    } else if code == 1 {
        "Created audit-only reports: at least one branch was rejected; no feature table or allowlist exported.\n"
    } else {
        "Created separate FET branch features, numeric predictors and complete audits. No model was trained.\n"
    })?;
    Ok(code)
}

/// Match argparse's unique long-option prefixes without exposing raw tokens in
/// errors. Unknown options remain for the parser, so --help retains its normal
/// early-exit behavior; ambiguous prefixes are rejected, never guessed.
fn normalize_workflow_args(tool: &str, argv: &[String]) -> Res<Vec<String>> {
    let mut allowed = if tool == "rram-features" {
        vec!["input-dir", "output-dir", "max-bracket-span-V"]
    } else {
        FET_OPTIONS.to_vec()
    };
    allowed.extend(["accept-measurement-assumptions", "help"]);
    let mut normalized = Vec::with_capacity(argv.len());
    let mut positional = false;
    for argument in argv {
        if argument == "--" {
            positional = true;
        }
        if let Some(option) = argument.strip_prefix("--").filter(|_| !positional) {
            let (name, value) = option
                .split_once('=')
                .map(|(k, v)| (k, Some(v)))
                .unwrap_or((option, None));
            if !allowed.contains(&name) {
                let matches: Vec<&&str> =
                    allowed.iter().filter(|key| key.starts_with(name)).collect();
                if matches.len() > 1 {
                    return err("ambiguous workflow option; see --help");
                }
                if matches.len() == 1 {
                    normalized.push(format!(
                        "--{}{}",
                        matches[0],
                        value.map(|v| format!("={}", v)).unwrap_or_default()
                    ));
                    continue;
                }
            }
        }
        normalized.push(argument.clone());
    }
    Ok(normalized)
}

fn write_stdout(text: &str) -> Res<()> {
    let mut stdout = std::io::stdout().lock();
    stdout
        .write_all(text.as_bytes())
        .and_then(|_| stdout.flush())
        .map_err(|_| "unable to write workflow summary".into())
}

fn parse_workflow_args(tool: &str, argv: &[String]) -> Res<Args> {
    let allowed = if tool == "rram-features" {
        &["input-dir", "output-dir", "max-bracket-span-V"][..]
    } else {
        FET_OPTIONS
    };
    let mut input = None;
    let mut options = HashMap::new();
    let mut flags = HashSet::new();
    let mut i = 0;
    let mut positional = false;
    while i < argv.len() {
        let arg = &argv[i];
        if arg == "--" && !positional {
            positional = true;
            i += 1;
            continue;
        }
        if let Some(option) = arg.strip_prefix("--").filter(|_| !positional) {
            let (key, value) = option
                .split_once('=')
                .map(|(k, v)| (k, Some(v)))
                .unwrap_or((option, None));
            if key == "accept-measurement-assumptions" && value.is_none() {
                flags.insert(key.to_string());
            } else if allowed.contains(&key) {
                let value = if let Some(value) = value {
                    value.to_string()
                } else {
                    i += 1;
                    argv.get(i)
                        .filter(|v| !v.starts_with("--"))
                        .ok_or("invalid workflow arguments; see --help")?
                        .clone()
                };
                options.insert(key.to_string(), value);
            } else {
                return err("invalid workflow arguments; see --help");
            }
        } else if tool == "fet-features" && input.is_none() {
            input = Some(PathBuf::from(arg));
        } else {
            return err("invalid workflow arguments; see --help");
        }
        i += 1;
    }
    for &key in allowed {
        if !options.contains_key(key) {
            return err("required workflow arguments missing; see --help");
        }
    }
    if tool == "rram-features" {
        input = Some(PathBuf::from(&options["input-dir"]));
    }
    Ok(Args {
        input: input.ok_or("input CSV path required; see --help")?,
        options,
        flags,
    })
}

fn private_directory(path: &Path) -> Res<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path).map_err(|_| {
        "unable to create new private directory; existing destinations are never overwritten".into()
    })
}

#[cfg(not(unix))]
fn private_file(path: &Path) -> Res<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    options
        .open(path)
        .map_err(|_| "unable to create a new private output file".into())
}
#[cfg(unix)]
fn open_directory(path: &Path) -> Res<File> {
    use std::os::unix::fs::OpenOptionsExt;
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| "unable to open new private directory".into())
}
#[cfg(unix)]
fn private_file_at(directory: &File, name: &str) -> Res<File> {
    use std::ffi::CString;
    use std::os::fd::{AsRawFd, FromRawFd};
    let filename = CString::new(name.as_bytes()).map_err(|_| "invalid internal output filename")?;
    let fd = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            filename.as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            0o600,
        )
    };
    if fd < 0 {
        return err("unable to create a new private output file");
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// Private temporary snapshot files are exclusively created, never user output.
struct Snapshot {
    directory: PathBuf,
    #[cfg(unix)]
    handle: File,
    files: Vec<String>,
}
impl Snapshot {
    fn new() -> Res<Self> {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| "unable to create private input snapshot")?
            .as_nanos();
        for _ in 0..100 {
            let path = std::env::temp_dir().join(format!(
                "smlt-features-{}-{}-{}",
                std::process::id(),
                stamp,
                SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            if private_directory(&path).is_ok() {
                #[cfg(unix)]
                let handle = open_directory(&path)?;
                return Ok(Self {
                    directory: path,
                    #[cfg(unix)]
                    handle,
                    files: vec![],
                });
            }
        }
        err("unable to create private input snapshot")
    }
    fn write(&mut self, name: &str, data: &[u8]) -> Res<PathBuf> {
        let path = self.directory.join(name);
        #[cfg(unix)]
        let mut stream = private_file_at(&self.handle, name)?;
        #[cfg(not(unix))]
        let mut stream = private_file(&path)?;
        // Claim only files whose exclusive creation succeeded. A failed create
        // must not authorize later deletion of a pre-existing file.
        self.files.push(name.to_string());
        stream
            .write_all(data)
            .map_err(|_| "unable to write complete input snapshot")?;
        Ok(path)
    }
}
impl Drop for Snapshot {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            use std::ffi::CString;
            use std::os::fd::AsRawFd;
            use std::os::unix::fs::MetadataExt;
            for name in &self.files {
                if let Ok(name) = CString::new(name.as_bytes()) {
                    // Pin cleanup to the directory we own. Never follow a
                    // renamed/replaced path to somebody else's files.
                    unsafe {
                        libc::unlinkat(self.handle.as_raw_fd(), name.as_ptr(), 0);
                    }
                }
            }
            if let (Ok(path), Ok(owned)) = (
                fs::symlink_metadata(&self.directory),
                self.handle.metadata(),
            ) {
                if path.dev() == owned.dev() && path.ino() == owned.ino() {
                    let _ = fs::remove_dir(&self.directory);
                }
            }
        }
        #[cfg(not(unix))]
        {
            for name in &self.files {
                let _ = fs::remove_file(self.directory.join(name));
            }
            let _ = fs::remove_dir(&self.directory);
        }
    }
}

fn write_outputs(destination: &Path, payloads: &Payloads) -> Res<()> {
    private_directory(destination)?;
    // An I/O error deliberately leaves the new partial directory, just as the
    // reference. Never delete, replace, merge, or rename over an output path.
    #[cfg(unix)]
    {
        let directory = open_directory(destination)?;
        for (name, data) in payloads {
            // Pin the created directory, so changing its parent path cannot
            // redirect subsequent output writes through a symlink.
            let mut stream = private_file_at(&directory, name)?;
            stream
                .write_all(data)
                .map_err(|_| "unable to write complete new output")?;
        }
    }
    #[cfg(not(unix))]
    for (name, data) in payloads {
        private_file(&destination.join(name))?
            .write_all(data)
            .map_err(|_| "unable to write complete new output")?;
    }
    Ok(())
}

fn sorted_json(value: &Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .iter()
                .map(|(k, v)| (k.clone(), sorted_json(v)))
                .collect::<BTreeMap<_, _>>()
                .into_iter()
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.iter().map(sorted_json).collect()),
        _ => value.clone(),
    }
}
fn json_bytes(value: &Value) -> Res<Vec<u8>> {
    let mut bytes = serde_json::to_vec_pretty(&sorted_json(value))
        .map_err(|_| "unable to serialize workflow report")?;
    bytes.push(b'\n');
    Ok(bytes)
}

/// CPython-compatible shortest float presentation for CSV (scientific
/// exponents have an explicit sign and at least two digits).
fn float_text(value: f64) -> String {
    let text = format!("{:?}", value);
    if let Some((mantissa, exponent)) = text.split_once('e') {
        let exponent: i32 = exponent.parse().expect("finite float exponent");
        format!("{}e{:+03}", mantissa, exponent)
    } else {
        text
    }
}
fn csv_cell(value: &Value) -> Res<String> {
    match value {
        Value::String(s) => Ok(s.clone()),
        Value::Number(n) if n.is_f64() => {
            Ok(float_text(n.as_f64().ok_or("nonfinite workflow output")?))
        }
        Value::Number(n) => Ok(n.to_string()),
        Value::Null => Ok(String::new()),
        _ => err("unsupported workflow CSV value"),
    }
}
fn csv_bytes(columns: &[&str], rows: &[Value]) -> Res<Vec<u8>> {
    fn append(output: &mut String, cells: &[String]) {
        for (index, cell) in cells.iter().enumerate() {
            if index > 0 {
                output.push(',');
            }
            // Python csv.writer with lineterminator="\n" only quotes newline,
            // comma and quote; accepted workflow IDs cannot contain CR/LF.
            if cell.contains([',', '"', '\n']) {
                output.push('"');
                output.push_str(&cell.replace('"', "\"\""));
                output.push('"');
            } else {
                output.push_str(cell);
            }
        }
        output.push('\n');
    }
    let mut output = String::new();
    append(
        &mut output,
        &columns.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
    );
    for row in rows {
        append(
            &mut output,
            &columns
                .iter()
                .map(|name| csv_cell(&row[*name]))
                .collect::<Res<Vec<_>>>()?,
        );
    }
    Ok(output.into_bytes())
}
fn source_hashes(fet: bool) -> Value {
    let mut files: Vec<(&str, &[u8])> = vec![
        ("src/workflows.rs", include_bytes!("workflows.rs")),
        ("src/common.rs", include_bytes!("common.rs")),
        (
            "src/reference_constants.json",
            include_bytes!("reference_constants.json"),
        ),
        ("Cargo.lock", include_bytes!("../Cargo.lock")),
    ];
    if fet {
        files.extend([
            (
                "src/vth_constant_current.rs",
                &include_bytes!("vth_constant_current.rs")[..],
            ),
            (
                "src/transfer_log_slope.rs",
                &include_bytes!("transfer_log_slope.rs")[..],
            ),
        ]);
    } else {
        files.extend([
            (
                "src/iv_readpoint.rs",
                &include_bytes!("iv_readpoint.rs")[..],
            ),
            ("src/rram_window.rs", &include_bytes!("rram_window.rs")[..]),
        ]);
    }
    Value::Object(
        files
            .into_iter()
            .map(|(name, data)| (name.to_string(), json!(sha256(data))))
            .collect(),
    )
}
fn runtime() -> Value {
    json!({"implementation":"native Rust", "package_version":env!("CARGO_PKG_VERSION"), "source_fingerprint_scope":"compile-time embedded Rust workflow, invoked core modules, common helpers, reference constants and Cargo.lock; no Python execution"})
}
fn native_args(path: PathBuf, options: &[(&str, &str)], flags: &[&str]) -> Args {
    Args {
        input: path,
        options: options
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        flags: flags.iter().map(|v| v.to_string()).collect(),
    }
}
fn safe_identifier(text: &str) -> Res<String> {
    let value = identifier(text, 128, false)?;
    if value.starts_with(['=', '+', '-', '@']) {
        return err("identifiers must be unpadded, control-free and spreadsheet-safe");
    }
    Ok(value)
}
fn numeric(text: &str) -> Res<Rat> {
    let exact = decimal(text)?;
    finite(&exact)?;
    Ok(exact)
}
fn row_value(row: &Row) -> Value {
    let mut value = json!(row.data);
    value["source_row"] = json!(row.line_end);
    value
}
fn median(mut values: Vec<f64>) -> Res<f64> {
    if values.is_empty() {
        return err("cannot aggregate an empty device");
    }
    values.sort_by(f64::total_cmp);
    let n = values.len();
    if n % 2 == 1 {
        Ok(values[n / 2])
    } else {
        finite(&((f64rat(values[n / 2 - 1]) + f64rat(values[n / 2])) / rat(2)))
    }
}
fn array<'a>(value: &'a Value, name: &str) -> Res<&'a [Value]> {
    value[name]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| "unsupported extractor report schema".into())
}
fn number_value(value: &Value) -> Res<f64> {
    value
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| "nonfinite or missing extractor numeric output".into())
}

fn build_rram(args: &Args) -> Res<Payloads> {
    let span_input = args.req("max-bracket-span-V")?;
    let span = numeric(span_input)?;
    if span <= rat(0) {
        return err("maximum bracket span must be positive");
    }
    let span_float = finite(&span)?;
    let raw: BTreeMap<&str, Vec<u8>> = RRAM_INPUTS
        .iter()
        .map(|&name| Ok((name, read_regular(&args.input.join(name), MAX_BYTES)?)))
        .collect::<Res<_>>()?;
    let (manifest, _) = parse_rows(&raw["manifest.csv"], MANIFEST, true, 10000)?;
    let (condition_rows, _) = parse_rows(&raw["conditions.csv"], CONDITIONS, true, 10000)?;
    let (measurements, _) = parse_rows(&raw["iv_sweeps.csv"], IV, true, 10000)?;
    let mut conditions = BTreeMap::new();
    for row in &condition_rows {
        let key = safe_identifier(row.get("condition"))?;
        safe_identifier(row.get("protocol"))?;
        if conditions.contains_key(&key) {
            return err("duplicate condition declaration");
        }
        let voltage = numeric(row.get("read_voltage_V"))?;
        let temperature = numeric(row.get("temperature_K"))?;
        let delay = numeric(row.get("read_delay_ms"))?;
        if voltage.is_zero() || temperature <= rat(0) || delay < rat(0) {
            return err("invalid read voltage, temperature, or read delay declaration");
        }
        conditions.insert(key, row);
    }
    let mut indexed: BTreeMap<Vec<String>, &Row> = BTreeMap::new();
    let mut pairs: BTreeMap<(String, String), BTreeMap<String, &Row>> = BTreeMap::new();
    let mut devices: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut replicate_keys = BTreeMap::new();
    let mut used_conditions = BTreeSet::new();
    for row in &manifest {
        for &field in MANIFEST {
            safe_identifier(row.get(field))?;
        }
        if !["HRS", "LRS"].contains(&row.get("state")) {
            return err("state must be explicitly HRS or LRS");
        }
        let key = group_key(row, &IV[..3])?;
        if indexed.insert(key, row).is_some() {
            return err("duplicate manifest measurement key");
        }
        if !conditions.contains_key(row.get("condition")) {
            return err("manifest condition has no declaration");
        }
        used_conditions.insert(row.get("condition"));
        let id: Vec<String> = ["batch", "wafer", "condition"]
            .iter()
            .map(|v| row.get(v).to_string())
            .collect();
        if devices
            .get(row.get("device"))
            .is_some_and(|existing| existing != &id)
        {
            return err("device identity or condition changes across repeated acquisitions");
        }
        devices.insert(row.get("device").to_string(), id);
        let pair = pairs
            .entry((row.get("device").to_string(), row.get("cycle").to_string()))
            .or_default();
        if pair.insert(row.get("state").to_string(), row).is_some() {
            return err("duplicate state for a device/cycle pair");
        }
        let repeat = (
            row.get("device").to_string(),
            row.get("replicate").to_string(),
        );
        if replicate_keys
            .get(&repeat)
            .is_some_and(|old| *old != row.get("cycle"))
        {
            return err("replicate identifier is reused across different cycles");
        }
        replicate_keys.insert(repeat, row.get("cycle"));
    }
    if used_conditions != conditions.keys().map(String::as_str).collect() {
        return err("unused condition declaration");
    }
    for pair in pairs.values() {
        if pair.len() != 2 {
            return err("manifest must declare one HRS and one LRS branch per cycle");
        }
        if pair["HRS"].get("replicate") != pair["LRS"].get("replicate") {
            return err("HRS and LRS replicate declarations do not match");
        }
    }
    let mut actual_keys = BTreeSet::new();
    for row in &measurements {
        for &field in &IV[..3] {
            safe_identifier(row.get(field))?;
        }
        for &field in &IV[3..] {
            numeric(row.get(field))?;
        }
        actual_keys.insert(group_key(row, &IV[..3])?);
    }
    if actual_keys != indexed.keys().cloned().collect() {
        return err(
            "measurement and manifest keys must match exactly; missing or extra acquisition",
        );
    }
    let mut snapshot = Snapshot::new()?;
    let iv_path = snapshot.write("iv.csv", &raw["iv_sweeps.csv"])?;
    let targets: BTreeMap<Vec<String>, f64> = indexed
        .iter()
        .map(|(key, row)| {
            let condition = conditions[row.get("condition")];
            Ok((
                key.clone(),
                number_float(condition.get("read_voltage_V"), &rat(1))?,
            ))
        })
        .collect::<Res<_>>()?;
    let (groups, row_count) = crate::iv_readpoint::load_csv(&iv_path, "V", "A").map_err(|_| {
        "I-V input or derived resistance exceeds the upstream tool's supported range"
    })?;
    if row_count != measurements.len()
        || groups.keys().cloned().collect::<BTreeSet<_>>() != actual_keys
    {
        return err("I-V extraction did not preserve declared acquisition keys");
    }
    let mut results = vec![];
    let mut resistance_rows = vec![];
    let mut lineage = vec![];
    for (key, declaration) in &indexed {
        let condition = conditions[declaration.get("condition")];
        let result =
            crate::iv_readpoint::extract_group(key, &groups[key], targets[key], Some(span_float));
        if result["status"] != "ok" {
            return err("I-V extraction rejected an acquisition; inspect the original branch with iv-readpoint");
        }
        let target = targets[key];
        let current = number_value(&result["current_A"])?;
        if current == 0.0 || (current > 0.0) != (target > 0.0) {
            return err("V/I requires nonzero current with the same sign as read voltage");
        }
        let resistance = finite(&(f64rat(target) / f64rat(current))).map_err(|_| {
            "I-V input or derived resistance exceeds the upstream tool's supported range"
        })?;
        if resistance <= 0.0 {
            return err("V/I resistance must be positive and finite");
        }
        results.push(result.clone());
        resistance_rows.push(json!({"device":key[0],"cycle":key[1],"state":declaration.get("state"),"read_condition":declaration.get("condition"),"read_voltage":condition.get("read_voltage_V"),"resistance":float_text(resistance)}));
        lineage.push(json!({"device":key[0],"cycle":key[1],"branch":key[2],"replicate":declaration.get("replicate"),"state":declaration.get("state"),"condition":declaration.get("condition"),"manifest_source_row":declaration.line_end,"condition_source_row":condition.line_end,"iv_source_rows":result["source_rows"],"resistance_input_source_row":resistance_rows.len()+1}));
    }
    let resistance_csv = csv_bytes(RESISTANCE, &resistance_rows)?;
    let resistance_path = snapshot.write("resistance_input.csv", &resistance_csv)?;
    let mut windows = crate::rram_window::run(&native_args(
        resistance_path,
        &[
            ("voltage-unit", "V"),
            ("resistance-unit", "ohm"),
            ("hrs-label", "HRS"),
            ("lrs-label", "LRS"),
        ],
        &[],
    ))
    .map_err(|_| "derived resistance input was rejected by rram-window")?;
    windows["input"]["source"] = json!("resistance_input.csv");
    if windows["counts"]["excluded_groups"] != 0 {
        return err("rram-window excluded a declared pair");
    }
    let observations = array(&windows, "pairs")?;
    let window_keys: BTreeSet<(String, String)> = observations
        .iter()
        .map(|p| {
            Ok((
                p["device"]
                    .as_str()
                    .ok_or("invalid RRAM pair identity")?
                    .to_string(),
                p["cycle"]
                    .as_str()
                    .ok_or("invalid RRAM pair identity")?
                    .to_string(),
            ))
        })
        .collect::<Res<_>>()?;
    if window_keys != pairs.keys().cloned().collect() || observations.len() != pairs.len() {
        return err("RRAM pairing did not preserve the declared acquisition set");
    }
    let mut features = vec![];
    let mut device_lineage = vec![];
    for (device, ids) in devices {
        let values: Vec<&Value> = observations
            .iter()
            .filter(|p| p["device"] == device)
            .collect();
        let hrs = median(
            values
                .iter()
                .map(|p| number_value(&p["hrs"]["resistance_ohm"]))
                .collect::<Res<_>>()?,
        )?;
        let lrs = median(
            values
                .iter()
                .map(|p| number_value(&p["lrs"]["resistance_ohm"]))
                .collect::<Res<_>>()?,
        )?;
        let ratio = median(
            values
                .iter()
                .map(|p| number_value(&p["resistance_ratio"]))
                .collect::<Res<_>>()?,
        )?;
        features.push(json!({"batch":ids[0],"wafer":ids[1],"device":device,"condition":ids[2],"split_group":ids[0],"read_voltage_V":conditions[&ids[2]].get("read_voltage_V"),"cycle_count":values.len(),"ratio_below_one_cycle_count":values.iter().filter(|p| p["ratio_below_one"]==true).count(),"hrs_resistance_median_ohm":hrs,"lrs_resistance_median_ohm":lrs,"resistance_ratio_median":ratio}));
        let cycles: BTreeSet<&str> = values
            .iter()
            .map(|p| p["cycle"].as_str().unwrap())
            .collect();
        let replicates: BTreeSet<&str> = manifest
            .iter()
            .filter(|row| row.get("device") == device)
            .map(|row| row.get("replicate"))
            .collect();
        device_lineage.push(json!({"device":device,"cycles":cycles,"replicates":replicates}));
    }
    let provenance = json!({"schema_version":2,"example_version":"0.1.0","runtime":runtime(),
        "inputs_sha256":raw.iter().map(|(name,data)| (name.to_string(),sha256(data))).collect::<BTreeMap<_,_>>(),"code_sha256":source_hashes(false),
        "parameters":{"max_bracket_span_V_input":span_input,"input_voltage_unit":"V","input_current_unit":"A","measurement_assumptions_declared":true},
        "conversion":"R_static_ohm = signed V_read_V / signed I_read_A; no absolute values or state inference",
        "aggregation":"median within each device and fixed condition, never across devices",
        "independent_unit":"user-declared physical device; independence is not verified",
        "split_group":"batch; examples provide grouping metadata, not a train/test split",
        "condition_declarations":conditions.values().map(|row| row_value(row)).collect::<Vec<_>>(),"device_lineage":device_lineage,"acquisition_lineage":lineage});
    let schema = json!({"schema_version":1,"row_key":["device"],"model_feature_allowlist":RRAM_FEATURES,
        "metadata_columns":RRAM_OUTPUT.iter().filter(|name| !RRAM_FEATURES.contains(name)).collect::<Vec<_>>(),"target_columns":[],
        "feature_units":{RRAM_FEATURES[0]:"ohm",RRAM_FEATURES[1]:"ohm",RRAM_FEATURES[2]:"dimensionless"},
        "warnings":["No target or trained model is included.","The ratio is derived from the same HRS/LRS observations; do not predict it from those resistances.","Counts, IDs, batch and condition are audit/splitting metadata, not default model inputs.","A row per declared device is not proof of statistical independence."]});
    let iv_report = json!({"schema_version":1,"tool":"iv-readpoint","version":"0.1.0",
        "invocation":"native iv_readpoint::load_csv(V,A) and extract_group per manifest condition; no branch filter",
        "input_rows":measurements.len(),"max_bracket_span_V":span_float,"assumptions_and_limits":constants("iv-readpoint","ASSUMPTIONS"),"results":results});
    Ok(IndexMap::from([
        ("features.csv".into(), csv_bytes(RRAM_OUTPUT, &features)?),
        ("feature_schema.json".into(), json_bytes(&schema)?),
        ("iv_readpoints.json".into(), json_bytes(&iv_report)?),
        ("resistance_input.csv".into(), resistance_csv),
        ("rram_windows.json".into(), json_bytes(&windows)?),
        ("provenance.json".into(), json_bytes(&provenance)?),
    ]))
}

fn report_by_key(report: &Value, columns: &[&str]) -> Res<IndexMap<Vec<String>, Value>> {
    let mut indexed = IndexMap::new();
    for result in array(report, "results")? {
        let key = columns
            .iter()
            .map(|name| {
                result[*name]
                    .as_str()
                    .map(str::to_string)
                    .ok_or_else(|| "unsupported extractor report identity".into())
            })
            .collect::<Res<Vec<_>>>()?;
        if indexed.insert(key, result.clone()).is_some() {
            return err("extractor report contains duplicate branch keys");
        }
    }
    Ok(indexed)
}

fn build_fet(args: &Args) -> Res<(Payloads, i32)> {
    let raw = read_regular(&args.input, MAX_BYTES)?;
    let input_hash = sha256(&raw);
    let mut snapshot = Snapshot::new()?;
    let path = snapshot.write("transfer.csv", &raw)?;
    let shared: Vec<(&str, &str)> = FET_OPTIONS
        .iter()
        .filter(|&&key| key != "output-dir")
        .map(|&key| Ok((key, args.req(key)?)))
        .collect::<Res<_>>()?;
    let threshold = crate::vth_constant_current::run(&native_args(
        path.clone(),
        &shared,
        &["accept-transfer-assumptions"],
    ))?;
    let logarithmic = crate::transfer_log_slope::run(&native_args(
        path,
        &shared,
        &["accept-log-transfer-assumptions"],
    ))?;
    assemble_fet(raw, &input_hash, threshold, logarithmic)
}

/// Separate assembly permits native unit tests to verify corrupt report joins.
fn assemble_fet(
    raw: Vec<u8>,
    input_hash: &str,
    threshold: Value,
    logarithmic: Value,
) -> Res<(Payloads, i32)> {
    for (report, tool) in [
        (&threshold, "vth-constant-current"),
        (&logarithmic, "transfer-log-slope"),
    ] {
        if report["schema_version"] != 1 || report["tool"] != tool {
            return err("unsupported extractor report schema");
        }
        if report["provenance"]["input_sha256"] != input_hash {
            return err("extractor input fingerprint does not match the shared snapshot");
        }
    }
    let threshold_keys = report_by_key(&threshold, FET_GROUP)?;
    let logarithmic_keys = report_by_key(&logarithmic, FET_GROUP)?;
    if threshold_keys.is_empty()
        || threshold_keys.keys().collect::<BTreeSet<_>>()
            != logarithmic_keys.keys().collect::<BTreeSet<_>>()
    {
        return err("extractor branches do not match one-to-one");
    }
    if threshold["counts"]["input_rows"] != logarithmic["counts"]["input_rows"]
        || threshold["counts"]["excluded_by_branch_filter"] != 0
        || threshold["counts"]["input_groups"] != json!(threshold_keys.len())
        || logarithmic["counts"]["input_groups"] != json!(logarithmic_keys.len())
    {
        return err("extractor counts do not match the complete shared input");
    }
    let complete = threshold_keys
        .values()
        .chain(logarithmic_keys.values())
        .all(|result| result["status"] == "ok");
    let mut rows = vec![];
    let mut lineage = vec![];
    let mut branch_statuses = vec![];
    for (key, criterion) in &threshold_keys {
        let log = &logarithmic_keys[key];
        let source_rows: Vec<Value> = array(criterion, "observations")?
            .iter()
            .map(|row| row["source_row"].clone())
            .collect();
        if json!(source_rows) != log["source_rows"] {
            return err("extractor source rows do not match");
        }
        let extraction = &criterion["extraction"];
        let mut status = identity(FET_GROUP, key);
        status["vth_status"] = criterion["status"].clone();
        status["vth_reason"] = criterion["reason"].clone();
        status["log_slope_status"] = log["status"].clone();
        status["log_slope_reason"] = log["reason"].clone();
        branch_statuses.push(status);
        let mut item = identity(FET_GROUP, key);
        item["feature_csv_source_row"] = if complete {
            json!(rows.len() + 2)
        } else {
            Value::Null
        };
        item["predictor_csv_source_row"] = item["feature_csv_source_row"].clone();
        item["transfer_source_rows"] = json!(source_rows);
        item["criterion_source_rows"] = if extraction.is_null() {
            json!([])
        } else {
            extraction["source_rows"].clone()
        };
        item["log_selected_source_rows"] = log["selected_source_rows"].clone();
        item["log_outside_window_source_rows"] = log["outside_window_source_rows"].clone();
        lineage.push(item);
        if !complete {
            continue;
        }
        if criterion["conditions"] != log["conditions"] {
            return err("extractor fixed conditions do not match");
        }
        if key
            .iter()
            .any(|value| value.starts_with(['=', '+', '-', '@']))
        {
            return err("CSV export requires spreadsheet-safe identifiers");
        }
        let mut row = identity(FET_GROUP, key);
        let conditions = criterion["conditions"]
            .as_object()
            .ok_or("unsupported extractor fixed-condition schema")?;
        row.as_object_mut().unwrap().extend(conditions.clone());
        row["split_group"] = json!(key[0]);
        row["signed_current_criterion_A"] =
            threshold["parameters"]["signed_current_criterion_A"].clone();
        row["window_min_V"] = logarithmic["parameters"]["window_min_V"].clone();
        row["window_max_V"] = logarithmic["parameters"]["window_max_V"].clone();
        row["max_bracket_span_V"] = threshold["parameters"]["max_bracket_span_V"].clone();
        row["branch_sample_count"] = criterion["sample_count"].clone();
        row["log_selected_sample_count"] = log["selected_sample_count"].clone();
        row["criterion_method"] = extraction["method"].clone();
        row["criterion_bracket_span_V"] = extraction["bracket_span_V"].clone();
        row[FET_FEATURES[0]] = extraction["gate_source_voltage_at_criterion_V"].clone();
        row[FET_FEATURES[1]] = log["fit"]["slope_decades_per_V"].clone();
        rows.push(row);
    }
    let schema = json!({"schema_version":1,"row_key":FET_GROUP,"model_feature_allowlist":FET_FEATURES,"metadata_columns":FET_METADATA,"target_columns":[],"feature_units":{FET_FEATURES[0]:"V",FET_FEATURES[1]:"decades/V"},"warnings":[
        "These two columns are candidate predictors, not a task-independent approval to use them.",
        "Never use a target or its algebraic proxy as an input feature. Redefine this allowlist for the actual endpoint.",
        "In particular, do not predict criterion VGS from that same criterion VGS, or slope from its reciprocal.",
        "Both features use the same transfer acquisition. Establish feature availability before a separately sourced endpoint.",
        "IDs, split groups, fixed biases, criterion/window choices, counts and extraction diagnostics are metadata, not model inputs.",
        "Log slope and its reciprocal are descriptive, not a certified subthreshold swing or identified physical regime.",
        "Rows are acquisition branches, not independent devices. Physical-device identity is lot/wafer/device.",
        "split_group equals lot as an example declaration, not evidence of independence between lots.",
        "No target, model, accuracy metric, uncertainty estimate or independent-device claim is supplied."]});
    let status = json!({"schema_version":1,"status":if complete {"complete"}else{"audit_only"},"feature_exported":complete,"branch_count":branch_statuses.len(),"feature_row_count":rows.len(),"branch_statuses":branch_statuses,"selection_policy":"all branches must pass both extractors; no partial-cohort feature export","interpretation":"software completion only; no physical regime or ML suitability certified"});
    let mut payloads: Payloads = IndexMap::from([
        ("vth_report.json".into(), json_bytes(&threshold)?),
        ("log_slope_report.json".into(), json_bytes(&logarithmic)?),
        ("transfer_input.csv".into(), raw),
        ("workflow_status.json".into(), json_bytes(&status)?),
    ]);
    if complete {
        let output: Vec<&str> = FET_METADATA.iter().chain(FET_FEATURES).copied().collect();
        payloads.insert("features.csv".into(), csv_bytes(&output, &rows)?);
        payloads.insert("predictors.csv".into(), csv_bytes(FET_FEATURES, &rows)?);
        payloads.insert("feature_schema.json".into(), json_bytes(&schema)?);
    }
    let physical_devices: BTreeSet<&[String]> =
        threshold_keys.keys().map(|key| &key[..3]).collect();
    let provenance = json!({"schema_version":2,"workflow":"fet-measurement-to-features","version":"0.1.0","runtime":runtime(),
        "input_sha256":input_hash,"retained_input":"transfer_input.csv","source_row_convention":threshold["provenance"]["source_row_convention"],"source_sha256":source_hashes(true),
        "output_sha256":payloads.iter().map(|(name,data)| (name.clone(),sha256(data))).collect::<BTreeMap<_,_>>(),
        "counts":{"input_rows":threshold["counts"]["input_rows"],"branch_count":branch_statuses.len(),"feature_rows":rows.len(),"distinct_physical_device_ids":physical_devices.len()},
        "row_unit":"one declared acquisition branch; not an independent device","physical_device_key":&FET_GROUP[..3],
        "row_alignment":"features.csv and predictors.csv have identical data-row order, when present",
        "calculator_schema":"exact seven-field version-1 allowlist accepted by groupbench --features-file",
        "invocation":{"vth":"native vth_constant_current::run(..., branch=None); full original schema retained","log_slope":"native transfer_log_slope::run(...); full original schema retained","measurement_assumptions_declared":true,"input_units":threshold["input_units"],"vth_parameters":threshold["parameters"],"log_slope_parameters":logarithmic["parameters"]},
        "acquisition_lineage":lineage});
    payloads.insert("provenance.json".into(), json_bytes(&provenance)?);
    Ok((payloads, if complete { 0 } else { 1 }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_float_csv_notation() {
        for (value, expected) in [
            (1e-8, "1e-08"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (3.0, "3.0"),
            (-0.0, "-0.0"),
        ] {
            assert_eq!(float_text(value), expected);
        }
    }
    #[test]
    fn csv_tuple_identifiers_and_quoting() {
        assert_eq!(
            csv_bytes(
                &["lot", "condition"],
                &[json!({"lot":"a,b|c","condition":"x\"y"})]
            )
            .unwrap(),
            b"lot,condition\n\"a,b|c\",\"x\"\"y\"\n"
        );
    }
    #[test]
    fn report_duplicate_keys_fail_closed() {
        let result = json!({"lot":"l","wafer":"w","device":"d","condition":"c","replicate":"r","branch":"b"});
        assert!(report_by_key(&json!({"results":[result,result]}), FET_GROUP).is_err());
    }
    #[test]
    fn median_does_not_overflow_midpoint() {
        assert_eq!(median(vec![1e308, 1e308]).unwrap(), 1e308);
        assert_eq!(median(vec![1.0, 3.0]).unwrap(), 2.0);
    }
    #[test]
    fn required_assumptions_and_options_are_not_inferred() {
        assert!(parse_workflow_args("rram-features", &[]).is_err());
        assert!(parse_workflow_args("fet-features", &["data.csv".into()]).is_err());
        assert!(safe_identifier("=formula").is_err());
        assert!(safe_identifier(" lot").is_err());
    }

    const TRANSFER: &[u8] = b"lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current\nl,w,d,c,r,b,300,0.05,0,0,1\nl,w,d,c,r,b,300,0.05,0,0.1,10\nl,w,d,c,r,b,300,0.05,0,0.2,100\nl,w,d,c,r,b,300,0.05,0,0.3,1000\nl,w,d,c,r,b,300,0.05,0,0.4,10000\n";
    fn reports(path: PathBuf) -> (Value, Value) {
        let options = [
            ("voltage-unit", "V"),
            ("current-unit", "nA"),
            ("criterion-current", "50"),
            ("criterion-current-unit", "nA"),
            ("current-polarity", "positive"),
            ("max-bracket-span", "0.1"),
            ("window-min", "0.1"),
            ("window-max", "0.4"),
        ];
        (
            crate::vth_constant_current::run(&native_args(
                path.clone(),
                &options,
                &["accept-transfer-assumptions"],
            ))
            .unwrap(),
            crate::transfer_log_slope::run(&native_args(
                path,
                &options,
                &["accept-log-transfer-assumptions"],
            ))
            .unwrap(),
        )
    }
    #[test]
    fn corrupt_fet_report_integrity_fails_closed() {
        let mut snapshot = Snapshot::new().unwrap();
        let path = snapshot.write("transfer.csv", TRANSFER).unwrap();
        let (threshold, logarithmic) = reports(path);
        let input_hash = sha256(TRANSFER);
        assert!(assemble_fet(
            TRANSFER.to_vec(),
            &input_hash,
            threshold.clone(),
            logarithmic.clone()
        )
        .is_ok());
        for corruption in 0..9 {
            let mut log = logarithmic.clone();
            let mut vth = threshold.clone();
            match corruption {
                0 => log["results"][0]["branch"] = json!("different"),
                1 => {
                    let duplicate = log["results"][0].clone();
                    log["results"].as_array_mut().unwrap().push(duplicate);
                }
                2 => log["provenance"]["input_sha256"] = json!("wrong"),
                3 => log["schema_version"] = json!(2),
                4 => log["counts"]["input_rows"] = json!(0),
                5 => log["results"][0]["source_rows"] = json!([]),
                6 => log["results"][0]["conditions"]["temperature_K"] = json!(301),
                7 => vth["counts"]["excluded_by_branch_filter"] = json!(1),
                8 => log["counts"]["input_groups"] = json!(2),
                _ => unreachable!(),
            }
            assert!(
                assemble_fet(TRANSFER.to_vec(), &input_hash, vth, log).is_err(),
                "corruption {} was accepted",
                corruption
            );
        }
    }
    #[test]
    fn shared_snapshot_survives_original_source_mutation() {
        let mut original = Snapshot::new().unwrap();
        let source = original.write("original.csv", TRANSFER).unwrap();
        let raw = read_regular(&source, MAX_BYTES).unwrap();
        let mut snapshot = Snapshot::new().unwrap();
        let retained = snapshot.write("transfer.csv", &raw).unwrap();
        fs::write(&source, b"original changed after bounded snapshot\n").unwrap();
        let (threshold, logarithmic) = reports(retained);
        let expected = sha256(TRANSFER);
        assert_eq!(threshold["provenance"]["input_sha256"], expected);
        assert_eq!(logarithmic["provenance"]["input_sha256"], expected);
        let (payloads, code) = assemble_fet(raw, &expected, threshold, logarithmic).unwrap();
        assert_eq!(code, 0);
        assert_eq!(payloads["transfer_input.csv"], TRANSFER);
    }
    #[test]
    fn output_io_failure_retains_new_partial_directory() {
        let temporary = Snapshot::new().unwrap();
        let destination = temporary.directory.join("output");
        let payloads = IndexMap::from([
            ("first.csv".to_string(), b"kept\n".to_vec()),
            (
                "missing/second.csv".to_string(),
                b"cannot create\n".to_vec(),
            ),
        ]);
        assert!(write_outputs(&destination, &payloads).is_err());
        assert_eq!(fs::read(destination.join("first.csv")).unwrap(), b"kept\n");
        assert!(destination.is_dir());
        assert!(write_outputs(&destination, &Payloads::new()).is_err());
        // Test cleanup only. Production never removes a failed output directory.
        fs::remove_file(destination.join("first.csv")).unwrap();
        fs::remove_dir(destination).unwrap();
    }
    #[test]
    fn private_snapshot_is_removed_on_drop() {
        let directory;
        {
            let mut snapshot = Snapshot::new().unwrap();
            directory = snapshot.directory.clone();
            snapshot.write("snapshot.csv", b"original bytes").unwrap();
            assert!(directory.is_dir());
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                assert_eq!(
                    fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
                    0o700
                );
                assert_eq!(
                    fs::metadata(directory.join("snapshot.csv"))
                        .unwrap()
                        .permissions()
                        .mode()
                        & 0o777,
                    0o600
                );
            }
        }
        assert!(!directory.exists());
    }
    #[test]
    fn failed_snapshot_create_does_not_claim_preexisting_file() {
        let mut snapshot = Snapshot::new().unwrap();
        let directory = snapshot.directory.clone();
        let existing = directory.join("existing.csv");
        fs::write(&existing, b"not created by snapshot writer").unwrap();
        assert!(snapshot.write("existing.csv", b"overwrite denied").is_err());
        assert!(snapshot.files.is_empty());
        drop(snapshot);
        assert_eq!(
            fs::read(&existing).unwrap(),
            b"not created by snapshot writer"
        );
        fs::remove_file(existing).unwrap();
        fs::remove_dir(directory).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn snapshot_cleanup_does_not_follow_swapped_directory_path() {
        use std::os::unix::fs::symlink;
        let mut snapshot = Snapshot::new().unwrap();
        let path = snapshot.directory.clone();
        snapshot.write("snapshot.csv", b"owned snapshot").unwrap();
        let moved = path.with_extension("moved");
        fs::rename(&path, &moved).unwrap();
        let mut other = Snapshot::new().unwrap();
        let other_file = other
            .write("snapshot.csv", b"unrelated data must remain")
            .unwrap();
        symlink(&other.directory, &path).unwrap();
        drop(snapshot);
        assert_eq!(
            fs::read(&other_file).unwrap(),
            b"unrelated data must remain"
        );
        assert!(!moved.join("snapshot.csv").exists());
        assert!(path.is_symlink());
        fs::remove_file(path).unwrap();
        fs::remove_dir(moved).unwrap();
    }
}
