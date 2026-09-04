param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first (https://docs.astral.sh/uv/getting-started/installation/), then run launch.ps1 again.'
}
uv sync --locked
if ($LASTEXITCODE -ne 0) { throw 'Python dependency setup failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) {
    Push-Location -LiteralPath (Join-Path $projectRoot 'web')
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency setup failed.' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
$workbenchUrl = "http://127.0.0.1:$Port"
Write-Host "Virtual microscopy: $workbenchUrl"
Write-Host 'Press Ctrl+C to stop the local server.'
$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ArgumentList $workbenchUrl -ScriptBlock {
        param($url)
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1
                if ($health.status -eq 'ok' -and $health.version -eq '0.1.0') {
                    Start-Process $url
                    break
                }
            } catch { }
            Start-Sleep -Milliseconds 250
        }
    }
}
try {
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m uvicorn virtual_microscopy.server:app --host 127.0.0.1 --port $Port
} finally {
    if ($null -ne $browserJob) { $browserJob | Stop-Job; $browserJob | Remove-Job }
}
