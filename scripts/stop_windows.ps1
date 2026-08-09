<#
.SYNOPSIS
    Stop FinAlly (Windows). Idempotent - safe to run when nothing is running.

.DESCRIPTION
    Removes the container but NEVER the 'finally-data' volume: your cash
    balance, positions, trades and watchlist survive a stop/start cycle. To
    throw the database away deliberately:  docker volume rm finally-data
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Container = 'finally'
$Volume    = 'finally-data'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH."
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "The Docker daemon is not responding. Is Docker Desktop running?"
    exit 1
}

$existing = docker ps --all --quiet --filter "name=^/$Container$"
if (-not [string]::IsNullOrWhiteSpace($existing)) {
    docker rm --force $Container *> $null
    Write-Host "Stopped and removed the '$Container' container."
} else {
    Write-Host "No '$Container' container found - nothing to stop."
}

Write-Host "The '$Volume' volume was left intact; your portfolio is still there."
