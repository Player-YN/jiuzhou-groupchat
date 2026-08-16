# start-electron.ps1 - ASCII only (safe on Chinese Windows)
param(
    [switch]$Silent,
    [switch]$ForceRebuild,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ElectronDir = Join-Path $Root "desktop-electron"
$Lifecycle = Join-Path $PSScriptRoot "groupchat-lifecycle.ps1"
$ElectronExe = Join-Path $ElectronDir "node_modules\electron\dist\electron.exe"
$LockFile = Join-Path $Root "backend\.groupchat.lock"
$LaunchLog = Join-Path $ElectronDir "launch.log"

function Write-Log([string]$level, [string]$m) {
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $level, $m
    try {
        $dir = Split-Path $LaunchLog -Parent
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        Add-Content -Path $LaunchLog -Value $line -Encoding utf8
    } catch {}
    if (-not $Silent) {
        $color = "Cyan"
        if ($level -eq "FAIL") { $color = "Red" }
        elseif ($level -eq "PASS") { $color = "Green" }
        elseif ($level -eq "WARN") { $color = "Yellow" }
        Write-Host ("[{0}] {1}" -f $level, $m) -ForegroundColor $color
    }
}

function Write-Info([string]$m) { Write-Log "INFO" $m }
function Write-Pass([string]$m) { Write-Log "PASS" $m }
function Write-Fail([string]$m) { Write-Log "FAIL" $m }

function Show-ErrorBox([string]$msg) {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        [System.Windows.Forms.MessageBox]::Show($msg, "Jiuzhou", "OK", "Error") | Out-Null
    } catch {}
}

function Test-ServicesReady {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -ne 200) { return $false }
    } catch { return $false }
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", 3000, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000, $false)
        if (-not $ok) { try { $c.Close() } catch {}; return $false }
        $c.EndConnect($iar)
        $c.Close()
        return $true
    } catch { return $false }
}

function Stop-OurElectron {
    Get-Process -Name "electron" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Path -and ($_.Path -like "*desktop-electron*")) {
                Write-Info ("Stop electron PID {0}" -f $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
}

function Invoke-Lifecycle {
    param([string[]]$ArgsList)
    # Separate process so lifecycle "exit" does not kill this launcher
    $all = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Lifecycle) + $ArgsList
    $win = "Normal"
    if ($Silent) { $win = "Hidden" }
    Write-Info ("lifecycle {0}" -f ($ArgsList -join " "))
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $all -Wait -PassThru -WindowStyle $win
    if ($null -eq $p) { return 1 }
    Write-Info ("lifecycle exit={0}" -f $p.ExitCode)
    return [int]$p.ExitCode
}

try {
    try {
        $dir = Split-Path $LaunchLog -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Set-Content -Path $LaunchLog -Value ("# launch {0}" -f (Get-Date -Format o)) -Encoding utf8
    } catch {}

    Write-Info ("Launch Silent={0} ForceRebuild={1} ForceRestart={2}" -f $Silent, $ForceRebuild, $ForceRestart)
    Write-Info ("Root={0}" -f $Root)

    if (-not (Test-Path $Lifecycle)) {
        Write-Fail "Missing groupchat-lifecycle.ps1"
        if ($Silent) { Show-ErrorBox "Missing scripts\groupchat-lifecycle.ps1" }
        exit 1
    }
    if (-not (Test-Path (Join-Path $ElectronDir "package.json"))) {
        Write-Fail "Missing desktop-electron"
        if ($Silent) { Show-ErrorBox "Missing desktop-electron" }
        exit 1
    }

    if (-not (Test-Path $ElectronExe)) {
        Write-Info "npm install desktop-electron..."
        Push-Location $ElectronDir
        try {
            & npm.cmd install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        } finally { Pop-Location }
    }
    if (-not (Test-Path $ElectronExe)) {
        Write-Fail "electron.exe missing"
        if ($Silent) { Show-ErrorBox "electron.exe missing - see launch.log" }
        exit 1
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    if ((-not $ForceRestart) -and (Test-ServicesReady)) {
        Write-Info "Fast path: services already up"
        if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
        Stop-OurElectron
    } else {
        Write-Info "Cold start"
        $null = Invoke-Lifecycle @("-Action", "stop", "-Quiet")
        Stop-OurElectron
        Start-Sleep -Milliseconds 400
        if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }

        $startArgs = @("-Action", "start", "-Mode", "electron", "-Quiet")
        if ($ForceRebuild) { $startArgs += "-ForceRebuild" }
        $ecStart = Invoke-Lifecycle $startArgs
        if ($ecStart -ne 0) {
            Write-Fail ("Services failed exit={0}" -f $ecStart)
            $null = Invoke-Lifecycle @("-Action", "stop", "-Quiet")
            if ($Silent) { Show-ErrorBox ("Start failed exit={0}`nSee launch.log" -f $ecStart) }
            exit 1
        }
    }

    if (-not (Test-ServicesReady)) {
        Write-Fail "Services not ready"
        if ($Silent) { Show-ErrorBox "Backend/Frontend not ready. See launch.log" }
        exit 1
    }

    Write-Info ("Ready in {0:n1}s - open Electron" -f $sw.Elapsed.TotalSeconds)

    $p = Start-Process -FilePath $ElectronExe `
        -ArgumentList @(".", "--no-spawn") `
        -WorkingDirectory $ElectronDir `
        -PassThru `
        -WindowStyle Normal `
        -Wait

    $ec = 0
    if ($null -ne $p) { $ec = $p.ExitCode }

    Write-Info ("Electron exit={0}" -f $ec)
    $null = Invoke-Lifecycle @("-Action", "stop", "-Quiet")
    Write-Info ("Total {0:n1}s" -f $sw.Elapsed.TotalSeconds)

    if ($ec -ne 0 -and $null -ne $ec) {
        Write-Fail ("Electron exit {0}" -f $ec)
        exit $ec
    }
    Write-Pass "OK"
    exit 0
} catch {
    Write-Fail $_.Exception.Message
    if ($Silent) { Show-ErrorBox ("Launch error:`n{0}`n{1}" -f $_.Exception.Message, $LaunchLog) }
    exit 1
}
