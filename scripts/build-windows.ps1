# Run from any directory after installing requirements-build.txt and NSIS.
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $python = if ($env:MINDTRAIL_BUILD_PYTHON) { $env:MINDTRAIL_BUILD_PYTHON } else { 'python' }
    & $python src/installer_build.py windows @args
    if ($LASTEXITCODE -ne 0) { throw "Windows installer build failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}
