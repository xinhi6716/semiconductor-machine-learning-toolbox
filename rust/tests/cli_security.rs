use std::process::Command;
#[test]
fn invalid_options_never_echo_private_tokens() {
    let result = Command::new(env!("CARGO_BIN_EXE_smlt"))
        .args([
            "iv-grid-audit",
            "PRIVATE_SENTINEL_PATH",
            "--PRIVATE_SENTINEL_OPTION",
        ])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(result.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&result.stderr).contains("PRIVATE_SENTINEL"));
}
#[cfg(unix)]
#[test]
fn invalid_unicode_arguments_are_categorical_not_panics() {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;
    let result = Command::new(env!("CARGO_BIN_EXE_smlt"))
        .arg("iv-grid-audit")
        .arg(OsString::from_vec(b"\xffPRIVATE_SENTINEL".to_vec()))
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(result.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&result.stderr).contains("PRIVATE_SENTINEL"));
    assert!(!String::from_utf8_lossy(&result.stderr).contains("panicked"));
}
#[test]
fn all_ten_commands_explain_their_options() {
    for tool in [
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
    ] {
        let result = Command::new(env!("CARGO_BIN_EXE_smlt"))
            .args([tool, "--help"])
            .output()
            .unwrap();
        assert!(result.status.success());
        assert!(!result.stdout.is_empty());
    }
}
