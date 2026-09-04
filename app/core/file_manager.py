import os
import shutil
from pathlib import Path

from app.utils.logger import get_logger

log = get_logger("vanta.core.file_manager")


class FileManager:

    def __init__(self, default_dir: Path):
        self._default_dir = Path(default_dir)
        self._ensure_default_dir()

    def _ensure_default_dir(self):
        self._default_dir.mkdir(parents=True, exist_ok=True)

    @property
    def default_dir(self) -> Path:
        return self._default_dir

    def set_default_dir(self, path: Path):
        self._default_dir = Path(path)
        self._ensure_default_dir()

    def get_unique_path(self, filename: str, destination: Path | None = None) -> Path:
        dest = destination or self._default_dir
        dest.mkdir(parents=True, exist_ok=True)

        base_path = dest / filename
        if not base_path.exists():
            return base_path

        stem = base_path.stem
        suffix = base_path.suffix
        counter = 1

        while True:
            new_path = dest / f"{stem}_{counter}{suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    def safe_join(self, filename: str) -> str:
        safe_name = os.path.basename(filename)
        safe_name = safe_name.replace("..", "").replace("/", "_").replace("\\", "_")
        if not safe_name:
            safe_name = "download"
        return safe_name

    def file_exists(self, path: Path) -> bool:
        return Path(path).exists()

    def delete_file(self, path: Path) -> bool:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True
        except Exception as e:
            log.error("Failed to delete %s: %s", path, e)
            return False

    def get_disk_space(self, path: Path | None = None) -> tuple[int, int]:
        target = path or self._default_dir
        try:
            usage = shutil.disk_usage(target)
            return usage.free, usage.total
        except Exception:
            return 0, 0
