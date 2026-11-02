# Publisher-only prerequisites. This never authenticates or signs a file.
param([string]$OutputDirectory = "build/windows-signing")
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or -not [Environment]::Is64BitProcess) {
    throw 'Use 64-bit PowerShell 7 on Windows.'
}
$runtimes = dotnet --list-runtimes
if ($LASTEXITCODE -ne 0 -or -not ($runtimes -match '^Microsoft.NETCore.App 8\.')) {
    throw 'Install the Microsoft .NET 8 x64 runtime on this publisher runner.'
}
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
$signer = Get-ChildItem "$sdkRoot/*/x64/signtool.exe" |
    Where-Object { $_.VersionInfo.FileVersionRaw -ge [version]'10.0.22621.755' } |
    Sort-Object { $_.VersionInfo.FileVersionRaw } -Descending | Select-Object -First 1
if (-not $signer) { throw 'A supported Windows SDK x64 SignTool is required.' }
if (-not (Test-Path "$env:SystemRoot/System32/vcruntime140.dll")) {
    throw 'Install the Microsoft Visual C++ x64 runtime on this publisher runner.'
}
$lock = Get-Content (Join-Path $PSScriptRoot 'artifact-signing-client.lock.json') -Raw | ConvertFrom-Json
$folder = New-Item $OutputDirectory -ItemType Directory -Force
$archive = Join-Path $folder.FullName 'client.zip'
Invoke-WebRequest $lock.url -OutFile $archive
if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $lock.sha256) {
    throw 'Artifact Signing client checksum mismatch; setup stopped.'
}
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $folder.FullName 'client') -Force
$dlib = Join-Path $folder.FullName ("client/" + $lock.dlib)
if (-not (Test-Path $dlib -PathType Leaf)) { throw 'The pinned signing client is incomplete.' }
if ($env:GITHUB_ENV) {
    "AWEAR_WINDOWS_SIGNTOOL=$($signer.FullName)" | Add-Content $env:GITHUB_ENV -Encoding utf8
    "AWEAR_WINDOWS_ARTIFACT_DLIB=$dlib" | Add-Content $env:GITHUB_ENV -Encoding utf8
    $signer.DirectoryName | Add-Content $env:GITHUB_PATH -Encoding utf8
}
Write-Host "Verified $($lock.package) $($lock.version); SDK SignTool $($signer.VersionInfo.FileVersionRaw). No signing requested."
