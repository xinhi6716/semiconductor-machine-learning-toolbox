$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Output = Join-Path $Root ("demo-reports-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Output | Out-Null
$Binary = Join-Path $Root "smlt.exe"
$Cases = Get-Content -Raw (Join-Path $Root "examples/demo-cases.json") | ConvertFrom-Json
foreach ($Case in $Cases) {
    $Arguments = @($Case.tool, $Case.input) + @($Case.arguments) + @("--output", (Join-Path $Output ($Case.tool + ".json")))
    & $Binary @Arguments
    if ($LASTEXITCODE -ne $Case.expected_exit) { throw ("Unexpected exit for " + $Case.tool + ": " + $LASTEXITCODE) }
}
Write-Host ("All 10 synthetic demos passed. Reports: " + $Output)
