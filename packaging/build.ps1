# Build Chiku.exe (PyInstaller) and Chiku-Setup-<version>.exe (Inno Setup).
# Usage (from the repository root):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Requires: .venv with `pip install -e ".[packaging]"`, Inno Setup 6 (winget install JRSoftware.InnoSetup)
param([string]$BuildDir = $(if ($env:CHIKU_BUILD_DIR) { $env:CHIKU_BUILD_DIR } else { Join-Path $env:LOCALAPPDATA "Chiku-build" }))
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
# Build outside the source tree: synced folders (OneDrive) and antivirus lock fresh files.
$dist = Join-Path $BuildDir "dist"
$work = Join-Path $BuildDir "work"
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$version = & $py -c "import sys; sys.path.insert(0, 'src'); import chiku; print(chiku.__version__)"
Write-Host "Building Chiku $version"

# 1. Brand assets (icon, avatar, installer art) from the original mascot images
& $py scripts\build_assets.py
if ($LASTEXITCODE -ne 0) { throw "asset build failed" }

# 2. Windows version resource for the executables
$parts = ($version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Intruder-tech'),
    StringStruct('FileDescription', 'Chiku voice assistant'),
    StringStruct('FileVersion', '$version'),
    StringStruct('ProductName', 'Chiku'),
    StringStruct('ProductVersion', '$version'),
    StringStruct('LegalCopyright', 'GPL-3.0-or-later')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding UTF8 packaging\version_info.txt

# 3. Freeze the app
& $py -m PyInstaller packaging\chiku.spec --noconfirm --clean `
    --distpath $dist --workpath $work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 4. Smoke-test the frozen CLI before packaging it
& (Join-Path $dist "Chiku\chiku-cli.exe") --version-info
if ($LASTEXITCODE -ne 0) { throw "frozen app failed to start" }

# 5. Installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$version" "/DDistDir=$(Join-Path $dist 'Chiku')" packaging\chiku.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$setup = Get-Item "packaging\output\Chiku-Setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  $($setup.Name)" | Set-Content -Encoding ASCII "$($setup.FullName).sha256"
Write-Host "Installer: $($setup.FullName) ($([math]::Round($setup.Length / 1MB)) MB)"
Write-Host "SHA-256:  $hash"
