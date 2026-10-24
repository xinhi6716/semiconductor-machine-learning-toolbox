mod bosch_identity_audit;
mod common;
mod hall_response;
mod iv_grid_audit;
mod iv_loop_integral;
mod iv_readpoint;
mod rram_window;
mod sweep_segments;
mod tlm_extract;
mod transfer_log_slope;
mod vth_constant_current;
mod workflows;
use common::*;
const TOOLS: &[&str] = &[
    "iv-readpoint",
    "rram-window",
    "tlm-extract",
    "vth-constant-current",
    "hall-response",
    "sweep-segments",
    "transfer-log-slope",
    "iv-loop-integral",
    "bosch-identity-audit",
    "iv-grid-audit",
];
fn output(text: &str) -> Res<()> {
    use std::io::Write;
    let mut out = std::io::stdout().lock();
    out.write_all(text.as_bytes())
        .and_then(|_| out.flush())
        .map_err(|_| "unable to write output".into())
}
fn main() {
    let args: Res<Vec<String>> = std::env::args_os()
        .skip(1)
        .map(|v| {
            v.into_string()
                .map_err(|_| "command-line arguments must be valid UTF-8".to_string())
        })
        .collect();
    let code = match args.and_then(|a| execute(&a)) {
        Ok(c) => c,
        Err(e) => {
            use std::io::Write;
            let _ = writeln!(
                std::io::stderr().lock(),
                "error: {}. No complete report claimed.",
                e
            );
            2
        }
    };
    std::process::exit(code)
}
fn execute(args: &[String]) -> Res<i32> {
    if args.is_empty() || args[0] == "--help" || args[0] == "-h" {
        output(&format!("smlt 0.1.0: Native Rust Semiconductor Machine Learning Toolbox\n\nUsage: smlt <tool> INPUT.csv [options] (--stdout | --output NEW.json)\n\nTools:\n  {}\n\nNative feature workflows: rram-features, fet-features (use --output-dir).\nUse smlt <tool> --help for exact required units, assumptions and schema.\nAll results are descriptive; no trained ML model or laboratory validation.\nNo Python runtime or network access. Existing output paths are never overwritten.\n",TOOLS.join("\n  ")))?;
        return Ok(0);
    }
    if args[0] == "--version" {
        output("smlt 0.1.0 (native Rust)\n")?;
        return Ok(0);
    }
    let tool = args[0].as_str();
    if ["rram-features", "fet-features"].contains(&tool) {
        return workflows::execute(tool, &args[1..]);
    }
    if !TOOLS.contains(&tool) {
        return err("unknown tool; use --help");
    }
    let (options, flags, run): (&[&str], &[&str], fn(&Args) -> Res<Value>) = match tool {
        "iv-readpoint" => (
            &[
                "voltage-unit",
                "current-unit",
                "read-voltage",
                "read-voltage-unit",
                "branch",
                "max-bracket-span",
            ],
            &[],
            iv_readpoint::run,
        ),
        "rram-window" => (
            &["voltage-unit", "resistance-unit", "hrs-label", "lrs-label"],
            &[],
            rram_window::run,
        ),
        "tlm-extract" => (
            &["length-unit", "resistance-unit"],
            &["accept-linear-tlm-assumptions"],
            tlm_extract::run,
        ),
        "vth-constant-current" => (
            &[
                "voltage-unit",
                "current-unit",
                "criterion-current",
                "criterion-current-unit",
                "current-polarity",
                "branch",
                "max-bracket-span",
            ],
            &["accept-transfer-assumptions"],
            vth_constant_current::run,
        ),
        "hall-response" => (
            &["field-unit", "voltage-unit", "current-unit"],
            &["accept-hall-assumptions"],
            hall_response::run,
        ),
        "sweep-segments" => (&["axis", "coordinate-unit"], &[], sweep_segments::run),
        "transfer-log-slope" => (
            &[
                "voltage-unit",
                "current-unit",
                "window-min",
                "window-max",
                "current-polarity",
            ],
            &["accept-log-transfer-assumptions"],
            transfer_log_slope::run,
        ),
        "iv-loop-integral" => (
            &["voltage-unit", "current-unit"],
            &["accept-supplied-path-assumptions"],
            iv_loop_integral::run,
        ),
        "bosch-identity-audit" => (&["channel-metadata"], &[], bosch_identity_audit::run),
        "iv-grid-audit" => (&["voltage-unit"], &[], iv_grid_audit::run),
        _ => unreachable!(),
    };
    let abbreviations = [
        "iv-readpoint",
        "rram-window",
        "tlm-extract",
        "vth-constant-current",
    ]
    .contains(&tool);
    let has_version = ![
        "rram-window",
        "iv-loop-integral",
        "bosch-identity-audit",
        "iv-grid-audit",
    ]
    .contains(&tool);
    let mut allowed = options.to_vec();
    allowed.extend(flags);
    allowed.extend(["output", "stdout", "help"]);
    if has_version {
        allowed.push("version")
    }
    let mut normalized = vec![];
    let mut after_delimiter = false;
    for arg in &args[1..] {
        if arg == "--" {
            after_delimiter = true;
            normalized.push(arg.clone());
            continue;
        }
        if abbreviations && !after_delimiter && arg.starts_with("--") {
            let (name, tail) = arg[2..]
                .split_once('=')
                .map(|(n, t)| (n, Some(t)))
                .unwrap_or((&arg[2..], None));
            if !allowed.contains(&name) {
                let matches: Vec<&&str> = allowed.iter().filter(|v| v.starts_with(name)).collect();
                if matches.len() > 1 {
                    return err("ambiguous command-line option");
                };
                if matches.len() == 1 {
                    normalized.push(format!(
                        "--{}{}",
                        matches[0],
                        tail.map(|t| format!("={}", t)).unwrap_or_default()
                    ));
                    continue;
                }
            }
        }
        normalized.push(arg.clone());
    }
    let control = &normalized[..normalized
        .iter()
        .position(|a| a == "--")
        .unwrap_or(normalized.len())];
    if control.iter().any(|v| v == "--help" || v == "-h") {
        let help: Value = serde_json::from_str(include_str!("cli_help.json"))
            .map_err(|_| "internal help unavailable")?;
        output(help[tool].as_str().ok_or("internal help unavailable")?)?;
        return Ok(0);
    }
    if has_version && control.iter().any(|v| v == "--version") {
        output("0.1.0\n")?;
        return Ok(0);
    }
    let parsed = parse_args(&normalized, options, flags)?;
    let report = run(&parsed)?;
    let cap = match tool {
        "iv-loop-integral" => 64 * 1024 * 1024,
        "bosch-identity-audit" | "iv-grid-audit" => 16 * 1024 * 1024,
        _ => usize::MAX,
    };
    write_report(&report, &parsed, cap)?;
    Ok(i32::from(
        report["status"] == "GRID_FINDINGS"
            || ["excluded_groups", "rejected_loops", "rejected_groups"]
                .iter()
                .any(|key| report["counts"][*key].as_u64().unwrap_or(0) > 0),
    ))
}
