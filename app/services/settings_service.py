from app.database.connection import get_session
from app.database.models import SettingRecord
from app.utils.logger import get_logger

log = get_logger("vanta.services.settings")

DEFAULTS = {
    "download_dir": str(__import__("pathlib").Path.home() / "Downloads" / "VANTA"),
    "max_concurrent": "3",
    "speed_limit_enabled": "false",
    "speed_limit_value": "0",
    "theme": "dark",
    "launch_on_startup": "false",
    "check_for_updates": "true",
}


class SettingsService:

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        session = get_session()
        try:
            records = session.query(SettingRecord).all()
            for r in records:
                self._cache[r.key] = r.value
            for key, default in DEFAULTS.items():
                if key not in self._cache:
                    self._cache[key] = default
                    session.add(SettingRecord(key=key, value=default))
            session.commit()
        finally:
            session.close()

    def get(self, key: str, default: str | None = None) -> str:
        if key in self._cache:
            return self._cache[key]
        return default if default is not None else DEFAULTS.get(key, "")

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get(key, str(default))
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, str(default).lower())
        return val.lower() in ("true", "1", "yes")

    def set(self, key: str, value: str | int | bool):
        str_value = str(value)
        self._cache[key] = str_value

        session = get_session()
        try:
            record = session.get(SettingRecord, key)
            if record:
                record.value = str_value
            else:
                session.add(SettingRecord(key=key, value=str_value))
            session.commit()
        finally:
            session.close()

    def download_dir(self):
        from pathlib import Path
        return Path(self.get("download_dir"))

    def max_concurrent(self) -> int:
        return self.get_int("max_concurrent", 3)

    def theme(self) -> str:
        return self.get("theme", "dark")
