import time

from app.core.task_manager import DownloadTask, TaskStatus
from app.core.downloader import DownloadManager
from app.database.repositories import save_download_task
from app.utils.logger import get_logger

log = get_logger("vanta.services.persistence")

_PERSIST_INTERVAL = 5.0
_TERMINAL_STATES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.PAUSED,
}


class PersistenceService:

    def __init__(self, persist_interval: float = _PERSIST_INTERVAL):
        self._persist_interval = persist_interval
        self._last_persist: dict[str, float] = {}
        self._download_manager: DownloadManager | None = None

    def subscribe_to(self, download_manager: DownloadManager):
        self._download_manager = download_manager
        download_manager.add_progress_callback(self._on_task_update)

    def _on_task_update(self, task: DownloadTask):
        if task.status in _TERMINAL_STATES:
            self._persist(task)
        else:
            now = time.monotonic()
            last = self._last_persist.get(task.id, 0)
            if now - last >= self._persist_interval:
                self._persist(task)

    def _persist(self, task: DownloadTask):
        try:
            save_download_task(task)
            self._last_persist[task.id] = time.monotonic()
        except Exception as e:
            log.error("Failed to persist task %s: %s", task.id, e, exc_info=True)

    def flush(self):
        if self._download_manager is None:
            return

        for task in self._download_manager.download_tasks:
            self._persist(task)
