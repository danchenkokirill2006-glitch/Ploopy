from pathlib import Path
import tempfile
import zipfile


def extract_update_helper(archive, destination_dir=None):
    """Extract only the trusted, root-level update helper from a verified package."""
    if destination_dir is None:
        destination_dir = tempfile.mkdtemp(prefix="Ploopy-update-helper-")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as package:
        try:
            helper = package.getinfo("Apply-Update.ps1")
        except KeyError as exc:
            raise ValueError("В архиве обновления не найден помощник установки") from exc
        if helper.is_dir() or helper.file_size > 128 * 1024:
            raise ValueError("Помощник установки в архиве обновления повреждён")
        target = destination / "Apply-Update.ps1"
        target.write_bytes(package.read(helper))
    return target
