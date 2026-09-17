"""One-time download eligibility timing above the queue scheduler."""
from __future__ import annotations

import asyncio
import time

from app.core.queue_controller import QueueController
from app.core.task_manager import TaskStatus


class SchedulingService:
    """Wakes due tasks and hands them to the existing queue scheduler."""

    def __init__(self, controller: QueueController, clock=time.time):
        self._controller = controller
        self._clock = clock
        self._timer_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._timer_task is not None and not self._timer_task.done()

    def start(self):
        if not self.running:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timer_task = asyncio.create_task(self._run())

    def stop(self):
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def refresh(self):
        if self.running:
            self.stop()
            self.start()

    def track(self, task_id: str):
        self.refresh()

    async def process_due_tasks(self) -> int:
        now = self._clock()
        activated = 0
        for task in list(self._controller.tasks):
            if (
                task.status == TaskStatus.QUEUED
                and task.scheduled_at is not None
                and task.scheduled_at <= now
            ):
                if self._controller.activate_scheduled_task(task.id):
                    activated += 1
        return activated

    async def _run(self):
        try:
            while True:
                future_times = [
                    task.scheduled_at
                    for task in self._controller.tasks
                    if task.status == TaskStatus.QUEUED
                    and task.scheduled_at is not None
                    and task.scheduled_at > self._clock()
                ]
                if not future_times:
                    await asyncio.sleep(3600)
                    continue
                delay = max(0.0, min(future_times) - self._clock())
                await asyncio.sleep(delay)
                await self.process_due_tasks()
        except asyncio.CancelledError:
            raise