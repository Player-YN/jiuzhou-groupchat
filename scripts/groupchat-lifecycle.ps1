# groupchat-lifecycle.ps1
# Shared start/stop/lock for 九洲一号群 (Electron + browser).
# Fixed ports: backend 8000, frontend 3000.
param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",

    [ValidateSet("electron", "browser", "any")]
    [string]$Mode = "any",

    # When starting for browser: open default browser and block until Ctrl+C / window close
    [switch]$Hold,

    # Skip starting services (attach only) — rarely used
    [switch]$NoSpawn,

    # Caller (Electron) already wrote .groupchat.lock — skip lock acquire/check
    [switch]$AssumeLocked,

    # When Electron calls stop on quit, do not taskkill Electron itself
    [switch]$SkipElectron,

    # Force full next build even if .next/BUILD_ID exists (slow; for after big UI edits)
    [switch]$ForceRebuild,

    # Suppress console noise (still writes errors on failure)
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$BackendPidFile = Join-Path $Backend ".uvicorn.pid"
$FrontendPidFile = Join-Path $Frontend ".next_server.pid"
$LockFile = Join-Path $Backend ".groupchat.lock"
$PortsFile = Join-Path $Backend ".groupchat-ports.env"
$RuntimeConfig = Join-Path $Frontend "public\runtime-config.js"

$BackendPort = 8000
$FrontendPort = 3000
$ApiBase = "http://127.0.0.1:$BackendPort"
$WsBase = "ws://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"

function Write-Info([string]$Msg) {
    if ($Quiet) { return }
    Write-Host "[INFO] $Msg" -ForegroundColor Cyan
}
function Write-Pass([string]$Msg) {
    if ($Quiet) { return }
    Write-Host "[PASS] $Msg" -ForegroundColor Green
}
function Write-Fail([string]$Msg) { Write-Host "[FAIL] $Msg" -ForegroundColor Red }
function Write-Warn([string]$Msg) {
    if ($Quiet) { return }
    Write-Host "[WARN] $Msg" -ForegroundColor Yellow
}

function Get-Listeners([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-PidTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    # Never kill this PowerShell host by accident
    if ($ProcessId -eq $PID) { return }
    try {
        $null = & taskkill.exe /PID $ProcessId /T /F 2>$null
        Write-Info "Killed process tree PID $ProcessId"
    } catch {
        # already gone
    }
}

function Clear-Port([int]$Port) {
    $ids = Get-Listeners $Port
    foreach ($procId in $ids) {
        Write-Info "Freeing port $Port (PID $procId)"
        Stop-PidTree $procId
    }
    # small settle
    Start-Sleep -Milliseconds 300
}

function Clear-PidFile([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    try {
        $raw = (Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue).Trim()
        if ($raw -match '^\d+$') {
            Stop-PidTree ([int]$raw)
        }
    } catch {}
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Read-Lock {
    if (-not (Test-Path $LockFile)) { return $null }
    try {
        return (Get-Content -LiteralPath $LockFile -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Test-ProcessAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ActiveLock {
    $lock = Read-Lock
    if ($null -eq $lock) { return $null }
    $holder = [int]($lock.holderPid)

    # Holder dead → stale (PID may later be reused by an unrelated process)
    if (-not (Test-ProcessAlive $holder)) {
        Write-Warn "Stale lock (holder PID $holder dead) — removing"
        Remove-Lock
        return $null
    }

    # Ports free → not a real groupchat session (PID reuse / orphan lock)
    $beUp = (Get-Listeners $BackendPort).Count -gt 0
    $feUp = (Get-Listeners $FrontendPort).Count -gt 0
    if (-not $beUp -and -not $feUp) {
        Write-Warn "Stale lock (PID $holder alive but ports $BackendPort/$FrontendPort free) — removing"
        Remove-Lock
        return $null
    }

    return $lock
}

function Write-Lock([string]$RunMode) {
    $payload = [ordered]@{
        mode         = $RunMode
        holderPid    = $PID
        backendPort  = $BackendPort
        frontendPort = $FrontendPort
        startedAt    = (Get-Date).ToString("o")
        root         = $Root
    }
    ($payload | ConvertTo-Json) | Set-Content -LiteralPath $LockFile -Encoding utf8
}

function Remove-Lock {
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}

function Stop-GroupChat {
    Write-Info "Stopping services and freeing ports..."

    # Fast electron kill: by process name + Path only (NO full WMI process scan — hangs on some PCs)
    if (-not $SkipElectron) {
        Get-Process -Name "electron" -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $pp = $_.Path
                if ($pp -and ($pp -like "*desktop-electron*")) {
                    Stop-PidTree ([int]$_.Id)
                }
            } catch {
                # Path may be denied; still free ports below
            }
        }
    }

    Clear-PidFile $BackendPidFile
    Clear-PidFile $FrontendPidFile
    Clear-Port $BackendPort
    Clear-Port $FrontendPort

    if (Test-Path $PortsFile) {
        try {
            Get-Content $PortsFile | ForEach-Object {
                if ($_ -match '^\s*(BACKEND_PORT|FRONTEND_PORT)\s*=\s*(\d+)\s*$') {
                    $p = [int]$Matches[2]
                    if ($p -ne $BackendPort -and $p -ne $FrontendPort) {
                        Clear-Port $p
                    }
                }
            }
        } catch {}
    }

    Remove-Lock
    Write-Pass "Ports $BackendPort / $FrontendPort cleared"
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 1
    }
    return $false
}

function Wait-PortListen([int]$Port, [int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Get-Listeners $Port).Count -gt 0) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Ensure-FrontendBuild {
    $buildIdPath = Join-Path $Frontend ".next\BUILD_ID"
    $hasBuild = Test-Path $buildIdPath

    # Fast path (product default): reuse existing .next unless ForceRebuild.
    # Auto-rebuild-on-any-source-touch made every launch run `npm run build` (~1–3 min).
    if ($hasBuild -and -not $ForceRebuild) {
        Write-Info "Frontend build present — skip rebuild (use -ForceRebuild after UI edits)"
        return
    }

    if (-not $hasBuild) {
        Write-Info "No .next/BUILD_ID — running npm run build (first time, may take a few minutes)..."
    } else {
        Write-Info "ForceRebuild: running npm run build..."
    }

    Push-Location $Frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $buildIdPath)) {
        throw "Build finished but BUILD_ID still missing"
    }
    Write-Pass "Frontend production build ready"
}

function Start-Services {
    if (-not (Test-Path $Python)) {
        throw "Backend venv missing: $Python"
    }
    if (-not (Test-Path (Join-Path $Frontend "package.json"))) {
        throw "Frontend package.json missing"
    }

    # Always free fixed ports first (orphans / stale pid files)
    Clear-PidFile $BackendPidFile
    Clear-PidFile $FrontendPidFile
    Clear-Port $BackendPort
    Clear-Port $FrontendPort
    Start-Sleep -Milliseconds 500

    if ((Get-Listeners $BackendPort).Count -gt 0 -or (Get-Listeners $FrontendPort).Count -gt 0) {
        throw "Could not free ports $BackendPort / $FrontendPort — aborting"
    }

    Ensure-FrontendBuild

    $publicDir = Join-Path $Frontend "public"
    New-Item -ItemType Directory -Force -Path $publicDir | Out-Null
    Set-Content -Path $RuntimeConfig -Encoding utf8 -NoNewline -Value "window.__API_BASE__ = '$ApiBase'; window.__WS_URL__ = '$WsBase';"
    Set-Content -Path $PortsFile -Encoding ascii -Value @(
        "BACKEND_PORT=$BackendPort"
        "FRONTEND_PORT=$FrontendPort"
        "API_BASE=$ApiBase"
        "WS_BASE=$WsBase"
    )

    $env:USE_MOCK_LLM = "false"

    # Start backend + frontend in parallel (was serial: health wait then next)
    Write-Info "Starting uvicorn :$BackendPort + next :$FrontendPort (parallel)..."
    $be = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
        -WorkingDirectory $Backend `
        -RedirectStandardOutput (Join-Path $Backend "uvicorn_out.log") `
        -RedirectStandardError (Join-Path $Backend "uvicorn_err.log") `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $BackendPidFile -Encoding ascii -NoNewline -Value $be.Id

    $fe = Start-Process -FilePath "npx.cmd" `
        -ArgumentList @("next", "start", "-H", "127.0.0.1", "-p", "$FrontendPort") `
        -WorkingDirectory $Frontend `
        -RedirectStandardOutput (Join-Path $Frontend "next_start.log") `
        -RedirectStandardError (Join-Path $Frontend "next_stderr.log") `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $FrontendPidFile -Encoding ascii -NoNewline -Value $fe.Id

    $beOk = Wait-HttpOk "$ApiBase/health" 90
    $feOk = Wait-PortListen $FrontendPort 90
    if (-not $beOk) {
        Stop-PidTree $be.Id
        Stop-PidTree $fe.Id
        throw "Backend /health not ready (see backend\uvicorn_err.log)"
    }
    if (-not $feOk) {
        Stop-PidTree $be.Id
        Stop-PidTree $fe.Id
        throw "Frontend did not listen on $FrontendPort (see frontend\next_stderr.log)"
    }
    Write-Pass "Backend OK  $ApiBase  PID $($be.Id)"
    Write-Pass "Frontend OK http://127.0.0.1:$FrontendPort  PID $($fe.Id)"
}

# ---------------- main ----------------
switch ($Action) {
    "stop" {
        Stop-GroupChat
        exit 0
    }
    "status" {
        $lock = Get-ActiveLock
        $be = Get-Listeners $BackendPort
        $fe = Get-Listeners $FrontendPort
        Write-Host "lock=$([bool]$lock) mode=$($lock.mode) holder=$($lock.holderPid)"
        Write-Host "backend:$BackendPort listeners=$($be -join ',')"
        Write-Host "frontend:$FrontendPort listeners=$($fe -join ',')"
        exit 0
    }
    "start" {
        if (-not $AssumeLocked) {
            $existing = Get-ActiveLock
            if ($existing) {
                Write-Fail "九洲一号群 already running (mode=$($existing.mode), holder PID $($existing.holderPid))."
                Write-Info "Close that window first, or: powershell -File scripts\groupchat-lifecycle.ps1 -Action stop"
                exit 2
            }

            # Ports busy without a live lock → refuse (do NOT kill another instance)
            if (-not $NoSpawn) {
                $busyBe = (Get-Listeners $BackendPort).Count -gt 0
                $busyFe = (Get-Listeners $FrontendPort).Count -gt 0
                if ($busyBe -or $busyFe) {
                    Write-Fail "Port 8000/3000 busy but no valid lock. Orphan processes?"
                    Write-Info "Run: powershell -File scripts\groupchat-lifecycle.ps1 -Action stop"
                    exit 3
                }
            }

            Write-Lock $Mode
        }

        try {
            if (-not $NoSpawn) {
                # Start-Services clears fixed ports then launches (we hold exclusive right)
                Start-Services
            }

            if ($Mode -eq "browser" -and $Hold) {
                Write-Pass "Opening browser $FrontendUrl"
                Start-Process $FrontendUrl | Out-Null
                Write-Host ""
                Write-Host "============================================================" -ForegroundColor Green
                Write-Host " 九洲一号群 (浏览器版) 运行中" -ForegroundColor Green
                Write-Host "  $FrontendUrl" -ForegroundColor Green
                Write-Host "  关闭本窗口 或 按 Ctrl+C 将自动杀进程并释放端口" -ForegroundColor Yellow
                Write-Host "============================================================" -ForegroundColor Green
                Write-Host ""

                try {
                    while ($true) {
                        if (-not (Test-Path $LockFile)) { Write-Lock $Mode }
                        Start-Sleep -Seconds 2
                    }
                } finally {
                    Stop-GroupChat
                }
            }
            exit 0
        } catch {
            Write-Fail $_.Exception.Message
            Stop-GroupChat
            exit 1
        }
    }
}
