#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
out=$(mktemp -d "./demo-reports.XXXXXX")
binary=${SMLT_BINARY:-./smlt}
echo "Synthetic reports: $out"
status=0
"$binary" iv-readpoint "examples/iv-readpoint/synthetic_iv.csv" "--voltage-unit" "V" "--current-unit" "uA" "--read-voltage" "100" "--read-voltage-unit" "mV" --output "$out/iv-readpoint.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for iv-readpoint: $status" >&2; exit 2; }
status=0
"$binary" rram-window "examples/rram-window/synthetic.csv" "--voltage-unit" "V" "--resistance-unit" "ohm" "--hrs-label" "HRS" "--lrs-label" "LRS" --output "$out/rram-window.json" || status=$?
[ "$status" -eq 1 ] || { echo "Unexpected exit for rram-window: $status" >&2; exit 2; }
status=0
"$binary" tlm-extract "examples/tlm-extract/synthetic_tlm.csv" "--length-unit" "um" "--resistance-unit" "ohm" "--accept-linear-tlm-assumptions" --output "$out/tlm-extract.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for tlm-extract: $status" >&2; exit 2; }
status=0
"$binary" vth-constant-current "examples/vth-constant-current/synthetic_transfer.csv" "--voltage-unit" "V" "--current-unit" "uA" "--criterion-current" "1" "--criterion-current-unit" "uA" "--current-polarity" "positive" "--accept-transfer-assumptions" --output "$out/vth-constant-current.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for vth-constant-current: $status" >&2; exit 2; }
status=0
"$binary" hall-response "examples/hall-response/synthetic_hall.csv" "--field-unit" "T" "--voltage-unit" "mV" "--current-unit" "mA" "--accept-hall-assumptions" --output "$out/hall-response.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for hall-response: $status" >&2; exit 2; }
status=0
"$binary" sweep-segments "examples/sweep-segments/synthetic_hall_coordinates.csv" "--axis" "magnetic-flux-density" "--coordinate-unit" "mT" --output "$out/sweep-segments.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for sweep-segments: $status" >&2; exit 2; }
status=0
"$binary" transfer-log-slope "examples/transfer-log-slope/synthetic_transfer.csv" "--voltage-unit" "V" "--current-unit" "nA" "--current-polarity" "positive" "--window-min" "0.1" "--window-max" "0.4" "--accept-log-transfer-assumptions" --output "$out/transfer-log-slope.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for transfer-log-slope: $status" >&2; exit 2; }
status=0
"$binary" iv-loop-integral "examples/iv-loop-integral/01_triangle.csv" "--voltage-unit" "V" "--current-unit" "uA" "--accept-supplied-path-assumptions" --output "$out/iv-loop-integral.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for iv-loop-integral: $status" >&2; exit 2; }
status=0
"$binary" bosch-identity-audit "examples/bosch-identity-audit/synthetic_inventory.csv" "--channel-metadata" "examples/bosch-identity-audit/synthetic_channels.json" --output "$out/bosch-identity-audit.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for bosch-identity-audit: $status" >&2; exit 2; }
status=0
"$binary" iv-grid-audit "examples/iv-grid-audit/synthetic_equal.csv" "--voltage-unit" "V" --output "$out/iv-grid-audit.json" || status=$?
[ "$status" -eq 0 ] || { echo "Unexpected exit for iv-grid-audit: $status" >&2; exit 2; }
echo "All 10 synthetic demos produced their expected exit codes."
