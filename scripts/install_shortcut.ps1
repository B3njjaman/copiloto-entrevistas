# Creates a console-less Copiloto launcher on the Desktop and in the Start Menu.
# Run it once from a clone: powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
# If your virtualenv lives elsewhere (e.g. another drive), pass its folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1 -Venv D:\venvs\copiloto
param([string]$Venv = '')
$ErrorActionPreference = 'Stop'
# This script lives in scripts/, so the repo root is its parent. Deriving the
# base at runtime means the shortcut works from whatever path you cloned into.
$base   = Split-Path -Parent $PSScriptRoot
if (-not $Venv) { $Venv = Join-Path $base '.venv' }
$target = Join-Path $Venv 'Scripts\pythonw.exe'
$icon   = Join-Path $base 'assets\cluely.ico'
if (-not (Test-Path $target)) { throw "pythonw not found: $target" }
if (-not (Test-Path $icon))   { throw "icon not found: $icon" }

$dests = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Copiloto.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) 'Copiloto.lnk')
)
$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in $dests) {
  $sc = $ws.CreateShortcut($lnk)
  $sc.TargetPath       = $target
  $sc.Arguments        = 'src\main.py'
  $sc.WorkingDirectory = $base
  $sc.IconLocation     = "$icon,0"
  $sc.Description       = 'Copiloto - tu suscripcion de Claude en tus reuniones'
  $sc.WindowStyle       = 1
  $sc.Save()
  Write-Output "created $lnk"
}
