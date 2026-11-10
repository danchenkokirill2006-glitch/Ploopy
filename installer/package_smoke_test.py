from pathlib import Path
import json
import sys

package = Path(sys.argv[1]).resolve()
required = (
    "Ploopy.exe", "yt-dlp.exe", "Ploopy.png", "Ploopy.ico", "version.txt",
    "Ploopy-hero.png", "Ploopy-download-smooth.gif",
    "fonts/Manrope-Regular.ttf", "assets/lucide/icons/download.svg",
    "vendor/resvg_py/__init__.py",
)
missing = [path for path in required if not (package / path).is_file()]
if missing:
    raise SystemExit(f"Не хватает файлов пакета: {', '.join(missing)}")
settings = json.loads((package / "settings.json").read_text(encoding="utf-8"))
history = json.loads((package / "history.json").read_text(encoding="utf-8"))
if settings.get("download_hotkey") != "Ctrl + Shift + J" or history != []:
    raise SystemExit("В пакете должны быть чистые начальные настройки и пустая история.")
private = ("ui_customization.json", "ui_element_edits.json", "startup.log")
if any((package / name).exists() for name in private):
    raise SystemExit("В пакет попал пользовательский файл.")
for name in ("Install-Ploopy.ps1", "Apply-Update.ps1"):
    if not (package.parent / name).is_file():
        raise SystemExit(f"В пакете отсутствует {name}.")
print("Состав пакета и чистые начальные данные проверены.")
