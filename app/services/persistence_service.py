from app.core.task_manager import DownloadTask
from app.database.repositories import save_download_task
from app.utils.logger import get_logger

log = get_logger("vanta.services.persistence")


class PersistenceService:

    def __init__(self):
        self._subscribers: list = []

    def subscribe_to(self, download_manager):
        download_manager.add_progress_callback(self._on_task_update)

    def _on_task_update(self, task: DownloadTask):
        try:
            save_download_task(task)
        except Exception as e:
            log.error("Failed to persist task %s: %s", task.id, e, exc_info=True)
