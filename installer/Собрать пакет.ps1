$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyy-MM-dd-HHmmss'
$temporary = Join-Path $env:TEMP "Ploopy-package-$stamp"
$venv = Join-Path $temporary 'venv'
$stage = Join-Path $temporary 'stage'
$appOut = Join-Path $stage 'Ploopy'
$buildOut = Join-Path $temporary 'build'
$releaseDir = Join-Path $project 'release'
$releaseVersion = if ($env:PLOOPY_VERSION) { $env:PLOOPY_VERSION.TrimStart('v', 'V') } else { '1.0.0' }
New-Item -ItemType Directory -Force -Path $temporary, $stage, $appOut, $releaseDir | Out-Null

py -3.10 -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать среду сборки Python 3.10.' }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install --upgrade pip pyinstaller yt-dlp pystray pillow
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить компоненты сборки.' }

& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name Ploopy `
    --distpath $stage --workpath (Join-Path $buildOut 'app') --specpath $temporary `
    --icon (Join-Path $project 'Ploopy.ico') --paths (Join-Path $project 'vendor') `
    --hidden-import resvg_py --hidden-import resvg_py.resvg_py --hidden-import pystray._win32 `
    (Join-Path $project 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать приложение Ploopy.' }

& $python -m PyInstaller --noconfirm --clean --onefile --console --name yt-dlp `
    --distpath $appOut --workpath (Join-Path $buildOut 'ytdlp') --specpath $temporary `
    (Join-Path $PSScriptRoot 'yt_dlp_launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать модуль скачивания.' }

Copy-Item -LiteralPath (Join-Path $project 'Ploopy.png') -Destination $appOut
Copy-Item -LiteralPath (Join-Path $project 'Ploopy.ico') -Destination $appOut
Copy-Item -LiteralPath (Join-Path $project 'Ploopy-hero.png') -Destination $appOut
Copy-Item -LiteralPath (Join-Path $project 'Ploopy-download-smooth.gif') -Destination $appOut
Copy-Item -LiteralPath (Join-Path $project 'fonts') -Destination $appOut -Recurse
Copy-Item -LiteralPath (Join-Path $project 'assets') -Destination $appOut -Recurse
Copy-Item -LiteralPath (Join-Path $project 'vendor') -Destination $appOut -Recurse
$settings = '{"start_minimized":true,"run_at_login":false,"sound":true,"browser":"Автоматически","download_hotkey":"Ctrl + Shift + J"}'
[System.IO.File]::WriteAllText((Join-Path $appOut 'settings.json'), $settings, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $appOut 'history.json'), '[]', [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $appOut 'version.txt'), $releaseVersion, [System.Text.UTF8Encoding]::new($false))

Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Install-Ploopy.ps1') -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Apply-Update.ps1') -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Установить Ploopy.bat') -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'ИНСТРУКЦИЯ.txt') -Destination $stage
$exe = Join-Path $appOut 'Ploopy.exe'
$check = Start-Process -FilePath $exe -ArgumentList '--self-test' -PassThru -Wait
if ($check.ExitCode -ne 0) { throw "Проверка собранного приложения завершилась с кодом $($check.ExitCode)." }
$ytdlp = Join-Path $appOut 'yt-dlp.exe'
$checkYtDlp = Start-Process -FilePath $ytdlp -ArgumentList '--version' -PassThru -Wait -NoNewWindow
if ($checkYtDlp.ExitCode -ne 0) { throw "Проверка yt-dlp завершилась с кодом $($checkYtDlp.ExitCode)." }

$installTest = Join-Path $temporary 'install-test'
$installScript = Join-Path $stage 'Install-Ploopy.ps1'
$testShortcut = Join-Path $temporary 'test-shortcuts\Ploopy.lnk'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript -PackageRoot $stage -InstallDir $installTest -ShortcutPath $testShortcut -NoLaunch
if ($LASTEXITCODE -ne 0) { throw 'Тестовая установка завершилась с ошибкой.' }
if (-not (Test-Path -LiteralPath (Join-Path $installTest 'Ploopy.exe'))) { throw 'После установки нет Ploopy.exe.' }
if (-not (Test-Path -LiteralPath (Join-Path $installTest 'settings.json'))) { throw 'После установки нет начальных настроек.' }
if ((Get-Content -Raw -LiteralPath (Join-Path $installTest 'history.json')) -ne '[]') { throw 'История в чистой установке не пустая.' }
if (-not (Test-Path -LiteralPath (Join-Path $installTest 'Apply-Update.ps1'))) { throw 'После установки нет помощника обновления.' }
if (-not (Test-Path -LiteralPath $testShortcut)) { throw 'Установщик не создал ярлык в тестовой папке.' }

$installTest2 = Join-Path $temporary 'install-test-existing'
New-Item -ItemType Directory -Force -Path $installTest2 | Out-Null
[System.IO.File]::WriteAllText((Join-Path $installTest2 'settings.json'), '{"download_hotkey":"Alt + Q"}', [System.Text.UTF8Encoding]::new($false))
$existingShortcut = Join-Path $temporary 'test-shortcuts-existing\Ploopy.lnk'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript -PackageRoot $stage -InstallDir $installTest2 -ShortcutPath $existingShortcut -NoLaunch
if ($LASTEXITCODE -ne 0) { throw 'Тест обновления завершился с ошибкой.' }
if (-not (Get-Content -Raw -LiteralPath (Join-Path $installTest2 'settings.json')).Contains('Alt + Q')) { throw 'Установка перезаписала пользовательские настройки.' }

& $python (Join-Path $PSScriptRoot 'package_smoke_test.py') $appOut
if ($LASTEXITCODE -ne 0) { throw 'Проверка состава пакета завершилась с ошибкой.' }
$zipName = if ($env:PLOOPY_VERSION) { "Ploopy-$($env:PLOOPY_VERSION).zip" } else { "Ploopy-$stamp.zip" }
$zip = Join-Path $releaseDir $zipName
if (Test-Path -LiteralPath $zip) {
    $stem = [IO.Path]::GetFileNameWithoutExtension($zipName)
    $zip = Join-Path $releaseDir "$stem-$stamp.zip"
}
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
$updateShortcut = Join-Path $temporary 'update-test-shortcuts\Ploopy.lnk'
$bootstrapDir = Join-Path $temporary 'update-bootstrap'
& $python -c "import sys; from update_bootstrap import extract_update_helper; print(extract_update_helper(sys.argv[1], sys.argv[2]))" $zip $bootstrapDir
if ($LASTEXITCODE -ne 0) { throw 'Не удалось извлечь помощник обновления из архива.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bootstrapDir 'Apply-Update.ps1') `
    -Archive $zip -InstallDir $installTest2 -ProcessId 999999 -ShortcutPath $updateShortcut -NoRestart
if ($LASTEXITCODE -ne 0) { throw 'Тест обновления установленного приложения завершился с ошибкой.' }
if (-not (Get-Content -Raw -LiteralPath (Join-Path $installTest2 'settings.json')).Contains('Alt + Q')) { throw 'Обновление перезаписало пользовательские настройки.' }
if ((Get-Content -Raw -LiteralPath (Join-Path $installTest2 'version.txt')) -ne $releaseVersion) { throw 'После обновления установлен неверный номер версии.' }
Write-Output "Архив создан: $zip"
Write-Output "Размер: $([math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)) МБ"
Write-Output "Проверки пройдены. yt-dlp $(& $ytdlp --version)"
