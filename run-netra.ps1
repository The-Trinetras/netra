<#
.SYNOPSIS
    Start every local Netra service and report what actually came up.

.DESCRIPTION
    One command for the whole local stack: PostgreSQL, Neo4j, migrations, the
    API, the worker and the WPF client. Safe to re-run — anything already
    running is left alone, so this doubles as a status check.

    Two settings the repository .env does not carry yet are supplied here for
    the processes this script starts (they are NOT written to .env):

      NETRA_TRACING_MODE=ax     when ARIZE_SPACE_ID and ARIZE_API_KEY are set
      NETRA_NEO4J_URI/USER/PASSWORD for the local container

    The API and worker each open their own window so their logs stay readable
    and Ctrl+C stops just that one. Close a window to stop that service;
    'docker stop compose-postgres-test-1 netra-neo4j' stops the containers.

    PostgreSQL runs on tmpfs: stopping that container loses the schema, every
    account and every upload. Re-run this script and re-provision.

.PARAMETER NoClient
    Skip the WPF client and start the services only.

.PARAMETER AccessCode
    Also provision a student access code once the API is up.

.EXAMPLE
    .\run-netra.ps1
    .\run-netra.ps1 -NoClient -AccessCode
#>

[CmdletBinding()]
param(
    [switch]$NoClient,
    [switch]$AccessCode
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = '.venv\Scripts\python.exe'
$apiPort = 8000

function Write-Step { param($Text) Write-Host "`n== $Text" -ForegroundColor Cyan }
function Write-Ok { param($Text) Write-Host "   OK   $Text" -ForegroundColor Green }
function Write-Skip { param($Text) Write-Host "   --   $Text" -ForegroundColor DarkGray }
function Write-Warn { param($Text) Write-Host "   !!   $Text" -ForegroundColor Yellow }

function Test-Port { param($Port)
    $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Invoke-Native {
    <# Native tools log to stderr as a matter of course (alembic writes its INFO
       lines there), and under ErrorActionPreference='Stop' a redirected stderr
       line becomes a terminating NativeCommandError. Exit code is the truth. #>
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe $($Arguments -join ' ') exited with $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

function Wait-For { param($Test, $Seconds = 60, $What = 'service')
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Test) { return $true }
        Start-Sleep -Seconds 2
    }
    Write-Warn "$What did not come up within $Seconds seconds"
    return $false
}

function Start-InWindow { param($Title, $Command)
    # Child processes inherit this process environment, which is why .env is
    # loaded and the overrides applied before anything starts.
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList @(
        '-NoExit', '-Command', "`$host.UI.RawUI.WindowTitle = '$Title'; $Command"
    ) | Out-Null
}

# --- preconditions ---------------------------------------------------------

Write-Step 'Checking the worktree'
foreach ($required in @($python, '.env', 'load-env.ps1')) {
    if (-not (Test-Path $required)) { throw "missing $required (run this from the Netra worktree)" }
}
Write-Ok "python, .env and load-env.ps1 present in $PSScriptRoot"

# --- containers ------------------------------------------------------------

Write-Step 'PostgreSQL'
docker compose -f infrastructure\compose\docker-compose.test.yml up -d | Out-Null
if (Wait-For -What 'PostgreSQL' -Test {
        (docker inspect -f '{{.State.Health.Status}}' compose-postgres-test-1 2>$null) -eq 'healthy' }) {
    Write-Ok 'compose-postgres-test-1 healthy on 127.0.0.1:55432 (tmpfs: contents are lost when it stops)'
}

Write-Step 'Neo4j'
if (docker ps -a --filter 'name=^/netra-neo4j$' --format '{{.Names}}') {
    docker start netra-neo4j | Out-Null
    Write-Ok 'netra-neo4j started (existing container)'
} else {
    docker run -d --name netra-neo4j -p 127.0.0.1:7687:7687 -p 127.0.0.1:7474:7474 `
        -e NEO4J_AUTH=neo4j/netra_local_only neo4j:5.26.30 | Out-Null
    Write-Ok 'netra-neo4j created'
}
Wait-For -What 'Neo4j' -Test { Test-Port 7687 } | Out-Null

# --- environment -----------------------------------------------------------

Write-Step 'Environment'
. .\load-env.ps1

if (-not $env:NETRA_NEO4J_URI) {
    $env:NETRA_NEO4J_URI = 'bolt://127.0.0.1:7687'
    $env:NETRA_NEO4J_USER = 'neo4j'
    $env:NETRA_NEO4J_PASSWORD = 'netra_local_only'
    Write-Ok 'Neo4j settings supplied for this run (add them to .env to make it permanent)'
}

if ($env:ARIZE_SPACE_ID -and $env:ARIZE_API_KEY) {
    if ($env:NETRA_TRACING_MODE -ne 'ax') {
        $env:NETRA_TRACING_MODE = 'ax'
        Write-Ok 'AX tracing on for this run (add NETRA_TRACING_MODE=ax to .env to make it permanent)'
    }
} else {
    Write-Skip 'AX tracing off: ARIZE_SPACE_ID / ARIZE_API_KEY are not set'
}

$env:PYTHONPATH = 'api/src;worker/src'
$env:PYTHONIOENCODING = 'utf-8'

# --- migrations ------------------------------------------------------------

Write-Step 'Migrations'
Invoke-Native $python @('-m', 'alembic', '-c', 'api/alembic.ini', 'upgrade', 'head') | Out-Null
Write-Ok (Invoke-Native $python @('-m', 'alembic', '-c', 'api/alembic.ini', 'current') | Select-Object -Last 1)

# --- services --------------------------------------------------------------

Write-Step 'API'
if (Test-Port $apiPort) {
    Write-Skip "something is already listening on 127.0.0.1:$apiPort; leaving it alone"
} else {
    Start-InWindow -Title 'netra-api' -Command "$python -m uvicorn --env-file .env --app-dir api/src --factory netra_api.main:create_app --host 127.0.0.1 --port $apiPort"
    Wait-For -What 'API' -Test {
        try { (Invoke-WebRequest "http://127.0.0.1:$apiPort/health/live" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
        catch { $false }
    } | Out-Null
    Write-Ok "listening on http://127.0.0.1:$apiPort"
}

Write-Step 'Worker'
# Deliberately looks for the python process, not the window that launched it: a
# window whose worker has died would otherwise read as "running" forever and
# leave you with no worker at all. Two runs within a few seconds can therefore
# start a second worker, which is harmless here - jobs are claimed under
# PostgreSQL leases with idempotent effects, so a duplicate takes no extra work.
$workerRunning = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*netra_worker*' })
if ($workerRunning) {
    Write-Skip "already running (pid $($workerRunning.ProcessId -join ', '))"
} else {
    Start-InWindow -Title 'netra-worker' -Command "$python -m netra_worker.main"
    Write-Ok 'started'
}

# --- what actually came up -------------------------------------------------

Write-Step 'Status'
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$apiPort/health/live" -TimeoutSec 5
    # @() forces a real array: a PSMemberInfoCollection has no Count of its own,
    # so PowerShell would member-enumerate and hand back one Count per property.
    $registered = @($health.registered.PSObject.Properties)
    $missing = @($registered | Where-Object { -not $_.Value } | ForEach-Object { $_.Name })
    Write-Host ("   capabilities registered: {0}/{1}" -f ($registered.Count - $missing.Count), $registered.Count)
    if ($missing) { Write-Warn "unconfigured (these routes answer 503): $($missing -join ', ')" }

    $telemetry = Invoke-RestMethod "http://127.0.0.1:$apiPort/health/telemetry" -TimeoutSec 5
    Write-Host ("   tracing: enabled={0} exported={1} failed={2} dropped={3} config_errors={4}" -f `
        $telemetry.tracing_enabled, $telemetry.exported, $telemetry.export_failures,
        ($telemetry.dropped_queue_full + $telemetry.dropped_after_failure), $telemetry.configuration_errors)
    if ($telemetry.last_error_code) { Write-Warn "last tracing error: $($telemetry.last_error_code)" }
} catch {
    Write-Warn "could not read the API health endpoints: $($_.Exception.Message)"
}

if ($AccessCode) {
    Write-Step 'Student access code'
    $env:PYTHONPATH = 'api/src'
    Invoke-Native $python @('-m', 'netra_api.identity.provisioning', '--access-code',
        '--code-valid-days', '7', '--credential-valid-days', '120') | Select-Object -Last 1
    $env:PYTHONPATH = 'api/src;worker/src'
    Write-Host '   the code works once; pass --account-id to add another code to the same student'
}

if (-not $NoClient) {
    Write-Step 'WPF client'
    $env:NETRA_API_ENDPOINT = "ws://127.0.0.1:$apiPort/v1/ws"
    Start-InWindow -Title 'netra-client' -Command 'dotnet run --project client\src\Netra.Desktop'
    Write-Ok 'launching (build is clean, but a real sign-in through it has never been verified)'
}

Write-Host "`nNetra is up. The student path still calls OpenRouter, ElevenLabs, Deepgram and" -ForegroundColor Green
Write-Host "Pinecone over the network: local services, cloud providers." -ForegroundColor Green
