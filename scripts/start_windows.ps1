<#
.SYNOPSIS
    Start FinAlly (Windows). Idempotent - safe to run repeatedly.

.DESCRIPTION
    Builds the image if it is missing, runs the container against the named
    Docker volume 'finally-data', waits for health, and prints the URL.

.PARAMETER Build
    Force a rebuild of the image and recreate the container.

.PARAMETER Open
    Open the app in your default browser once it is healthy.

.PARAMETER Port
    Host port to publish (default 8000).

.EXAMPLE
    .\scripts\start_windows.ps1
.EXAMPLE
    .\scripts\start_windows.ps1 -Build -Open
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Open,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$Image     = 'finally:latest'
$Container = 'finally'
$Volume    = 'finally-data'   # named Docker volume holding the SQLite database

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# --- Preflight -------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH. Install Docker Desktop: https://docs.docker.com/get-docker/"
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "The Docker daemon is not responding. Is Docker Desktop running?"
    exit 1
}

# The container reads its configuration from --env-file, so the file has to
# exist. A blank OPENROUTER_API_KEY only disables chat (PLAN.md section 5), so
# we seed one from the template rather than refusing to start.
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example."
    Write-Host "  Add your OPENROUTER_API_KEY to enable the AI chat panel; everything else works without it."
}

# --- Image -----------------------------------------------------------------
$existingImage = docker images -q $Image
if ($Build -or [string]::IsNullOrWhiteSpace($existingImage)) {
    Write-Host "Building $Image ..."
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed."; exit 1 }
} else {
    Write-Host "Using existing image $Image (pass -Build to rebuild)."
}

# --- Container -------------------------------------------------------------
$running = docker ps --quiet --filter "name=^/$Container$"
if ((-not [string]::IsNullOrWhiteSpace($running)) -and (-not $Build)) {
    Write-Host "FinAlly is already running."
} else {
    $existing = docker ps --all --quiet --filter "name=^/$Container$"
    if (-not [string]::IsNullOrWhiteSpace($existing)) {
        Write-Host "Replacing the existing '$Container' container (the $Volume volume is untouched) ..."
        docker rm --force $Container *> $null
    }

    # This volume form must match docker-compose.yml, stop_windows.ps1, the mac
    # scripts, and the documented 'docker run' - one database, one entry point
    # shape (PLAN.md section 11).
    docker run --detach `
        --name $Container `
        --publish "$($Port):8000" `
        --env-file .env `
        --volume "$($Volume):/app/db" `
        --restart unless-stopped `
        $Image *> $null
    if ($LASTEXITCODE -ne 0) { Write-Error "docker run failed."; exit 1 }
    Write-Host "Started container '$Container'."
}

# --- Wait for health -------------------------------------------------------
$Url = "http://localhost:$Port"
Write-Host -NoNewline "Waiting for the app to become healthy "
for ($i = 0; $i -lt 60; $i++) {
    $state = docker inspect --format '{{.State.Status}}' $Container
    if ($LASTEXITCODE -ne 0) { $state = 'missing' }
    if ($state -ne 'running') {
        Write-Host ""
        Write-Host "Container is '$state'. Last log lines:"
        docker logs --tail 40 $Container
        exit 1
    }
    $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $Container
    if ($health -eq 'healthy' -or $health -eq 'none') { break }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 2
}
Write-Host ""

Write-Host ""
Write-Host "  FinAlly is running at $Url"
Write-Host "  Data lives in the Docker volume '$Volume' (docker volume rm $Volume resets it)."
Write-Host "  Logs:  docker logs -f $Container"
Write-Host "  Stop:  .\scripts\stop_windows.ps1"
Write-Host ""

if ($Open) {
    Start-Process $Url
}
