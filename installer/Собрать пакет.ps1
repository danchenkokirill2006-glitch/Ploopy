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

$buildPythonVersion = & python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $buildPythonVersion -ne '3.12') { throw 'Для сборки нужен Python 3.12, доступный через PATH.' }
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать среду сборки Python 3.12.' }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install --upgrade pip pyinstaller 'yt-dlp[default]' pystray pillow
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить компоненты сборки.' }

& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name Ploopy `
    --distpath $stage --workpath (Join-Path $buildOut 'app') --specpath $temporary `
    --icon (Join-Path $project 'Ploopy.ico') --paths (Join-Path $project 'vendor') `
    --hidden-import resvg_py --hidden-import resvg_py.resvg_py --hidden-import pystray._win32 `
    (Join-Path $project 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать приложение Ploopy.' }

& $python -m PyInstaller --noconfirm --clean --onefile --console --name yt-dlp `
    --distpath $appOut --workpath (Join-Path $buildOut 'ytdlp') --specpath $temporary `
    --collect-all yt_dlp_ejs `
    (Join-Path $PSScriptRoot 'yt_dlp_launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать модуль скачивания.' }

$denoRelease = Invoke-RestMethod -Uri 'https://api.github.com/repos/denoland/deno/releases/latest'
$denoVersion = $denoRelease.tag_name.TrimStart('v')
$denoAssetName = 'deno-x86_64-pc-windows-msvc.zip'
$denoAsset = $denoRelease.assets | Where-Object { $_.name -eq $denoAssetName } | Select-Object -First 1
$denoChecksumAsset = $denoRelease.assets | Where-Object { $_.name -eq "$denoAssetName.sha256sum" } | Select-Object -First 1
if (-not $denoAsset -or -not $denoChecksumAsset) { throw 'В последнем выпуске Deno не найдены архив Windows и его контрольная сумма.' }
$denoArchive = Join-Path $temporary "deno-$denoVersion.zip"
$denoExtract = Join-Path $temporary 'deno-extracted'
$denoUrl = $denoAsset.browser_download_url
Invoke-WebRequest -Uri $denoUrl -OutFile $denoArchive
$denoChecksumFile = Join-Path $temporary 'deno.sha256sum'
Invoke-WebRequest -Uri $denoChecksumAsset.browser_download_url -OutFile $denoChecksumFile
$denoChecksumText = Get-Content -Raw -LiteralPath $denoChecksumFile
$denoHashMatch = [regex]::Match($denoChecksumText, '(?im)^Hash\s*:\s*([a-f0-9]{64})')
if (-not $denoHashMatch.Success) { throw 'Не удалось прочитать контрольную сумму Deno.' }
$denoExpectedHash = $denoHashMatch.Groups[1].Value.ToLowerInvariant()
$denoActualHash = (Get-FileHash -LiteralPath $denoArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($denoActualHash -ne $denoExpectedHash) { throw 'Контрольная сумма Deno не совпала.' }
Expand-Archive -LiteralPath $denoArchive -DestinationPath $denoExtract -Force
$denoExecutable = Join-Path $denoExtract 'deno.exe'
if (-not (Test-Path -LiteralPath $denoExecutable -PathType Leaf)) { throw 'В архиве Deno не найден deno.exe.' }
Copy-Item -LiteralPath $denoExecutable -Destination (Join-Path $appOut 'deno.exe')

$ffmpegArchive = Join-Path $temporary 'ffmpeg-lgpl.zip'
$ffmpegExtract = Join-Path $temporary 'ffmpeg-extracted'
$ffmpegUrl = 'https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip'
Invoke-WebRequest -Uri $ffmpegUrl -OutFile $ffmpegArchive
$ffmpegChecksumFile = Join-Path $temporary 'ffmpeg-checksums.sha256'
Invoke-WebRequest -Uri 'https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/latest/checksums.sha256' -OutFile $ffmpegChecksumFile
$ffmpegAssetName = [IO.Path]::GetFileName($ffmpegUrl)
$ffmpegChecksumLine = Get-Content -LiteralPath $ffmpegChecksumFile | Where-Object { $_ -match [regex]::Escape($ffmpegAssetName) } | Select-Object -First 1
if (-not $ffmpegChecksumLine) { throw 'В списке контрольных сумм нет архива FFmpeg.' }
$ffmpegExpectedHash = ($ffmpegChecksumLine -split '\s+')[0].ToLowerInvariant()
$ffmpegActualHash = (Get-FileHash -LiteralPath $ffmpegArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ffmpegActualHash -ne $ffmpegExpectedHash) { throw 'Контрольная сумма FFmpeg не совпала.' }
Expand-Archive -LiteralPath $ffmpegArchive -DestinationPath $ffmpegExtract -Force
$ffmpegExecutable = Get-ChildItem -LiteralPath $ffmpegExtract -Filter 'ffmpeg.exe' -File -Recurse | Select-Object -First 1
if (-not $ffmpegExecutable) { throw 'В архиве FFmpeg не найден ffmpeg.exe.' }
$ffmpegOut = Join-Path $appOut 'ffmpeg'
New-Item -ItemType Directory -Force -Path $ffmpegOut | Out-Null
Copy-Item -Path (Join-Path $ffmpegExecutable.Directory.FullName '*') -Destination $ffmpegOut -Recurse -Force
$ffmpegRoot = $ffmpegExecutable.Directory.Parent.FullName
Get-ChildItem -LiteralPath $ffmpegRoot -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)' } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $appOut -Force
}
$ffmpegLicense = Join-Path $appOut 'COPYING.LGPLv2.1'
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0/COPYING.LGPLv2.1' -OutFile $ffmpegLicense
$denoLicense = Join-Path $appOut 'LICENSE-Deno.txt'
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/denoland/deno/v$denoVersion/LICENSE.md" -OutFile $denoLicense
$sitePackages = Join-Path $venv 'Lib\site-packages'
$ejsDistInfo = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'yt_dlp_ejs-*.dist-info' | Select-Object -First 1
if (-not $ejsDistInfo) { throw 'Не установлены скрипты yt-dlp-ejs.' }
$ejsLicensePath = Join-Path $ejsDistInfo.FullName 'licenses\LICENSE'
if (-not (Test-Path -LiteralPath $ejsLicensePath -PathType Leaf)) { throw 'Не найден текст лицензии yt-dlp-ejs.' }
Copy-Item -LiteralPath $ejsLicensePath -Destination (Join-Path $appOut 'LICENSE-yt-dlp-ejs.txt')
$ejsLibraryPath = Join-Path $sitePackages 'yt_dlp_ejs\yt\solver\lib.min.js'
$ejsLibrary = [System.IO.File]::ReadAllText($ejsLibraryPath)
$ejsLicenseBlock = [regex]::Match($ejsLibrary, '(?s)^/\*!.*?\*/')
if (-not $ejsLicenseBlock.Success) { throw 'Не найдены лицензии компонентов JavaScript-загрузчика.' }
[System.IO.File]::WriteAllText((Join-Path $appOut 'LICENSE-yt-dlp-ejs-dependencies.txt'), $ejsLicenseBlock.Value, [System.Text.UTF8Encoding]::new($false))
$thirdPartyNotices = @"
Сторонние компоненты в Ploopy

Deno $denoVersion — лицензия MIT; полный текст: LICENSE-Deno.txt.
Исходный код: https://github.com/denoland/deno/tree/v$denoVersion

FFmpeg 9.0 — сборка LGPL shared, без GPL-компонентов.
Текст лицензии: COPYING.LGPLv2.1.
Исходный код FFmpeg: https://ffmpeg.org/releases/ffmpeg-9.0.tar.xz
Сценарии сборки Windows: https://github.com/FFmpeg-Builds/FFmpeg-Builds

yt-dlp и yt-dlp-ejs включены в yt-dlp.exe.
Лицензия yt-dlp-ejs: LICENSE-yt-dlp-ejs.txt.
Лицензии Meriyah (ISC) и Astring (MIT): LICENSE-yt-dlp-ejs-dependencies.txt.
Исходный код и лицензии: https://github.com/yt-dlp/yt-dlp
"@
[System.IO.File]::WriteAllText((Join-Path $appOut 'THIRD-PARTY-NOTICES.txt'), $thirdPartyNotices, [System.Text.UTF8Encoding]::new($false))

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
Write-Output "Проверки пройдены. yt-dlp $(& $ytdlp --version); Deno $denoVersion; FFmpeg 9.0 LGPL."
