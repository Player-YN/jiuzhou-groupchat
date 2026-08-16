# rebuild-frontend.ps1 - ASCII only
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Frontend = Join-Path $Root "frontend"
$Log = Join-Path $Root "desktop-electron\rebuild.log"

function Log([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    Write-Host $line
    try {
        $dir = Split-Path $Log -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Add-Content -Path $Log -Value $line -Encoding utf8
    } catch {}
}

if (-not (Test-Path (Join-Path $Frontend "package.json"))) {
    Write-Host "[FAIL] frontend missing" -ForegroundColor Red
    exit 1
}

Log "npm run build (1-3 min)..."
Push-Location $Frontend
$ec = 0
try {
    & npm.cmd run build 2>&1 | ForEach-Object {
        Write-Host $_
        try { Add-Content -Path $Log -Value "$_" -Encoding utf8 } catch {}
    }
    $ec = $LASTEXITCODE
} finally {
    Pop-Location
}

$buildId = Join-Path $Frontend ".next\BUILD_ID"
if ($ec -ne 0 -or -not (Test-Path $buildId)) {
    Write-Host "[FAIL] build failed exit=$ec log=$Log" -ForegroundColor Red
    exit 1
}
Log ("BUILD_ID={0}" -f (Get-Content $buildId -Raw).Trim())
Write-Host "[PASS] rebuild OK" -ForegroundColor Green
exit 0
