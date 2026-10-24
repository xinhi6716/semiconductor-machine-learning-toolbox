$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Output = Join-Path $Root ("workflow-reports-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Output | Out-Null
& .\smlt.exe rram-features --input-dir examples/measurement-to-features/fixtures --output-dir (Join-Path $Output "rram") --max-bracket-span-V 0.11 --accept-measurement-assumptions
if ($LASTEXITCODE -ne 0) { throw "RRAM workflow failed" }
& .\smlt.exe fet-features examples/fet-measurement-to-features/fixtures/transfer.csv --voltage-unit V --current-unit nA --criterion-current 50 --criterion-current-unit nA --current-polarity positive --max-bracket-span 0.1 --window-min 0.1 --window-max 0.4 --accept-measurement-assumptions --output-dir (Join-Path $Output "fet")
if ($LASTEXITCODE -ne 0) { throw "FET workflow failed" }
Write-Host ("Both synthetic native workflows completed: " + $Output)
