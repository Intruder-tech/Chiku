# End-to-end installer check (current user, no admin):
#   install -> files/shortcut/uninstall entry -> frozen CLI runs -> GUI starts ->
#   upgrade to a newer build keeps user data -> silent uninstall removes the app, keeps user data.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\verify_install.ps1 -Setup <v1.exe> -Upgrade <v2.exe>
# It never runs the "delete my data" uninstall path: on a developer PC that would remove real
# settings, memories and models.
param([Parameter(Mandatory)] [string]$Setup, [Parameter(Mandatory)] [string]$Upgrade)
$ErrorActionPreference = "Stop"
$appId = "{7C5B2E6A-3F1D-4C8E-9B7A-2D4E6F8A1C3B}_is1"
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId"
$dir = Join-Path $env:TEMP ("chiku-install-test-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$startLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Chiku.lnk"
$dataFiles = @("$env:APPDATA\Chiku\settings.json", "$env:LOCALAPPDATA\Chiku\memory.db") | Where-Object { Test-Path $_ }
$results = [ordered]@{}

function Hashes { $h = @{}; foreach ($f in $dataFiles) { $h[$f] = (Get-FileHash $f).Hash }; $h }
function Check($name, [bool]$ok, $detail = "") { $results[$name] = $(if ($ok) { "PASS" } else { "FAIL" }) + $(if ($detail) { " ($detail)" } else { "" }) }

$before = Hashes

# 1. Install
$t = Measure-Command { Start-Process $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART", "/DIR=`"$dir`"", "/LOG=`"$dir-install.log`"" -Wait }
Check "install (silent, per-user)" (Test-Path "$dir\Chiku.exe") ("{0:n0} s" -f $t.TotalSeconds)
Check "both executables present" ((Test-Path "$dir\Chiku.exe") -and (Test-Path "$dir\chiku-cli.exe"))
Check "Start menu shortcut" (Test-Path $startLnk)
$reg = Get-ItemProperty $uninstKey -ErrorAction SilentlyContinue
Check "uninstall entry (HKCU)" ($null -ne $reg) "$($reg.DisplayName) $($reg.DisplayVersion)"
Check "no desktop shortcut unless chosen" (-not (Test-Path "$([Environment]::GetFolderPath('Desktop'))\Chiku.lnk"))

# 2. Frozen app runs
& "$dir\chiku-cli.exe" --version-info | Out-Host
Check "installed CLI: all components load" ($LASTEXITCODE -eq 0)

# 3. GUI starts and shows its window
$p = Start-Process "$dir\Chiku.exe" -PassThru
$deadline = (Get-Date).AddSeconds(45); $title = ""
while ((Get-Date) -lt $deadline -and -not $p.HasExited) {
  Start-Sleep -Milliseconds 500; $p.Refresh(); $title = $p.MainWindowTitle
  if ($title -like "Chiku*" -or $title -like "Welcome to Chiku*") { break }
}
Check "GUI window appears" ($title -like "*Chiku*") "title '$title'"
Get-Process -Id $p.Id -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
Start-Sleep 3
Get-Process Chiku -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force

# 4. Upgrade in place
$oldVer = $reg.DisplayVersion
Start-Process $Upgrade -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART", "/DIR=`"$dir`"" -Wait
$reg2 = Get-ItemProperty $uninstKey -ErrorAction SilentlyContinue
Check "upgrade replaces version" ($reg2.DisplayVersion -ne $oldVer) "$oldVer -> $($reg2.DisplayVersion)"
& "$dir\chiku-cli.exe" --version-info | Out-Null
Check "upgraded app runs" ($LASTEXITCODE -eq 0)
$afterUp = Hashes
Check "upgrade kept user data" (($before.Keys | Where-Object { $before[$_] -ne $afterUp[$_] }).Count -eq 0) "$($dataFiles.Count) file(s) compared"

# 5. Silent uninstall (default: keep personal data)
Start-Process "$dir\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
Start-Sleep 2
Check "uninstall removed program files" (-not (Test-Path "$dir\Chiku.exe"))
Check "uninstall removed Start menu shortcut" (-not (Test-Path $startLnk))
Check "uninstall removed uninstall entry" (-not (Test-Path $uninstKey))
$afterUn = Hashes
Check "uninstall kept user data by default" (($before.Keys | Where-Object { $before[$_] -ne $afterUn[$_] }).Count -eq 0)

$results.GetEnumerator() | ForEach-Object { "{0,-42} {1}" -f $_.Key, $_.Value }
if ($results.Values -match "^FAIL") { exit 1 }
