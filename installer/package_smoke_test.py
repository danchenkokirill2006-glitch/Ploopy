from pathlib import Path
import json
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from update_bootstrap import extract_update_helper

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
with tempfile.TemporaryDirectory(prefix="Ploopy-update-helper-test-") as temporary:
    temporary = Path(temporary)
    archive = temporary / "update.zip"
    helper_bytes = (package.parent / "Apply-Update.ps1").read_bytes()
    with zipfile.ZipFile(archive, "w") as update:
        update.writestr("Apply-Update.ps1", helper_bytes)
    extracted = extract_update_helper(archive, temporary / "extracted")
    if extracted.read_bytes() != helper_bytes:
        raise SystemExit("Не удалось извлечь помощник обновления из архива.")
    incomplete_archive = temporary / "incomplete-update.zip"
    with zipfile.ZipFile(incomplete_archive, "w") as update:
        update.writestr("README.txt", "incomplete")
    try:
        extract_update_helper(incomplete_archive, temporary / "missing")
    except ValueError:
        pass
    else:
        raise SystemExit("Не обнаружено отсутствие помощника в архиве обновления.")
print("Package contents and clean initial data verified.")
