param(
    [string]$PackageRoot = $PSScriptRoot,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\Ploopy'),
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ploopy.lnk'),
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PackageRoot 'Ploopy'
if (-not (Test-Path -LiteralPath (Join-Path $source 'Ploopy.exe'))) {
    throw "Не найдена папка приложения: $source"
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
# Keep settings, download history, and UI customizations from any existing install.
$excluded = @('settings.json', 'history.json', 'ui_customization.json', 'ui_element_edits.json', 'startup.log')
$preserve = @($excluded | Where-Object { Test-Path -LiteralPath (Join-Path $InstallDir $_) })
$copyArgs = @($source, $InstallDir, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
if ($preserve.Count -gt 0) {
    $copyArgs += '/XF'
    $copyArgs += $preserve
}
& robocopy @copyArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Не удалось скопировать файлы приложения (код $LASTEXITCODE)." }
$updaterSource = Join-Path $PackageRoot 'Apply-Update.ps1'
if (Test-Path -LiteralPath $updaterSource) {
    Copy-Item -LiteralPath $updaterSource -Destination (Join-Path $InstallDir 'Apply-Update.ps1') -Force
}
$exe = Join-Path $InstallDir 'Ploopy.exe'
$shortcutParent = Split-Path -Parent $ShortcutPath
New-Item -ItemType Directory -Force -Path $shortcutParent | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = $InstallDir
$shortcut.IconLocation = (Join-Path $InstallDir 'Ploopy.ico')
$shortcut.Description = 'Ploopy — загрузчик по ссылке'
$shortcut.Save()
Write-Output "Ploopy установлен: $InstallDir"
Write-Output "Ярлык создан: $ShortcutPath"
if (-not $NoLaunch) { Start-Process -FilePath $exe }
