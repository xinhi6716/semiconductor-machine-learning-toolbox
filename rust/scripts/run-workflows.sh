#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
binary=${SMLT_BINARY:-./smlt}
base=$(mktemp -d "./workflow-reports.XXXXXX")
"$binary" rram-features --input-dir examples/measurement-to-features/fixtures --output-dir "$base/rram" --max-bracket-span-V 0.11 --accept-measurement-assumptions
"$binary" fet-features examples/fet-measurement-to-features/fixtures/transfer.csv --voltage-unit V --current-unit nA --criterion-current 50 --criterion-current-unit nA --current-polarity positive --max-bracket-span 0.1 --window-min 0.1 --window-max 0.4 --accept-measurement-assumptions --output-dir "$base/fet"
echo "Both native synthetic feature workflows completed: $base"
