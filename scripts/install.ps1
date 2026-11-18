# Install the standalone kubectl-sift (no Python needed) on Windows.
#
#   irm https://raw.githubusercontent.com/OpeyemiAdeniji/KubeSift/main/scripts/install.ps1 | iex
#
# Options: $env:KUBESIFT_VERSION (e.g. v2.1.7; default latest), $env:KUBESIFT_INSTALL_DIR
# (default %LOCALAPPDATA%\kubesift), $env:KUBESIFT_SKIP_PATH=1 to leave PATH alone (CI). The download is checked
# against the published SHA-256 before installing, and the folder is added to your user PATH.
$ErrorActionPreference = 'Stop'
$repo = 'OpeyemiAdeniji/KubeSift'
$version = if ($env:KUBESIFT_VERSION) { $env:KUBESIFT_VERSION } else { 'latest' }
if ($version -eq 'latest') { $base = "https://github.com/$repo/releases/latest/download" }
else {
    if (-not $version.StartsWith('v')) { $version = "v$version" }
    $base = "https://github.com/$repo/releases/download/$version"
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw "Only 64-bit Intel/AMD Windows has a standalone binary; use: pipx install `"kubesift[all]`""
}
$archive = 'kubectl-sift_windows_amd64.zip'
$installDir = if ($env:KUBESIFT_INSTALL_DIR) { $env:KUBESIFT_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'kubesift' }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("kubesift-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host "Downloading $archive ($version)..."
    Invoke-WebRequest -UseBasicParsing "$base/$archive" -OutFile (Join-Path $tmp $archive)
    Invoke-WebRequest -UseBasicParsing "$base/$archive.sha256" -OutFile (Join-Path $tmp "$archive.sha256")
    $expected = ((Get-Content (Join-Path $tmp "$archive.sha256") -Raw).Trim() -split '\s+')[0].ToLower()
    $actual = (Get-FileHash (Join-Path $tmp $archive) -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "Checksum mismatch for $archive (expected $expected, got $actual); not installing" }
    Write-Host 'Checksum OK.'
    Expand-Archive (Join-Path $tmp $archive) -DestinationPath $tmp -Force
    if (Test-Path (Join-Path $installDir 'kubectl-sift')) { Remove-Item -Recurse -Force (Join-Path $installDir 'kubectl-sift') }
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Move-Item (Join-Path $tmp 'kubectl-sift') (Join-Path $installDir 'kubectl-sift')
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
$bin = Join-Path $installDir 'kubectl-sift'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $env:KUBESIFT_SKIP_PATH -and -not ($userPath -split ';' | Where-Object { $_ -eq $bin })) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ";$bin"), 'User')
    Write-Host "Added $bin to your user PATH (open a new terminal)."
}
$v = & (Join-Path $bin 'kubectl-sift.exe') version
Write-Host "Installed $v to $bin"
Write-Host 'Try: kubectl sift diagnose'
