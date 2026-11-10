param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ploopy.lnk'),
    [switch]$NoRestart
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Archive)) { throw "Не найден архив обновления: $Archive" }
$extractRoot = Join-Path $env:TEMP ("Ploopy-update-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $extractRoot | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $extractRoot
$current = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
if ($current) { Wait-Process -Id $ProcessId -Timeout 120 }
$installer = Join-Path $extractRoot 'Install-Ploopy.ps1'
if (-not (Test-Path -LiteralPath $installer)) { throw 'В архиве обновления нет установщика.' }
& $installer -PackageRoot $extractRoot -InstallDir $InstallDir -ShortcutPath $ShortcutPath -NoLaunch
if (-not $NoRestart) { Start-Process -FilePath (Join-Path $InstallDir 'Ploopy.exe') }
